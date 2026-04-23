# lemma lem:maximal_orbits_vs_open_subsets

## statement
Let \(S=\mathcal O_0+\mathfrak u\) and \(Z=\overline{\,G\cdot S\,}\subset \mathfrak g(\mathbb R)\). A real nilpotent orbit \(\mathcal O\subset Z\) is maximal for the closure order on the nilpotent orbits contained in \(Z\) if and only if \(\mathcal O\cap S\) contains a nonempty open subset of \(S\).

## proof
Write \(s=x'+n\in S\) with \(x'\in\mathcal O_0\) and \(n\in\mathfrak u\). The operator \(n\) preserves
\[
0\subset X\subset X^\perp\subset V
\]
and induces \(0\) on \(X\), \(V_0=X^\perp/X\), and \(V/X^\perp\). Hence \(s\) induces \(0\), \(x'\), and \(0\) on those quotients, so \(s\) is nilpotent. Therefore \(Z=\overline{G\cdot S}\) lies in the nilpotent cone. Since real nilpotent orbits are parametrized by signed Young diagrams, only finitely many such orbits occur in \(Z\).

If \(\mathcal O\subset Z\) is \(\preceq\)-maximal and not open in \(Z\), then some distinct orbit \(\mathcal O'\subset Z\) has \(\mathcal O\cap\overline{\mathcal O'}\neq\varnothing\). Because \(\overline{\mathcal O'}\) is \(G\)-stable and \(\mathcal O\) is one \(G\)-orbit, this forces \(\mathcal O\subset\overline{\mathcal O'}\), contradicting maximality. So every maximal orbit is open in \(Z\). Since \(G\cdot S\) is dense in \(Z\), such an orbit meets \(G\cdot S\), hence meets \(S\); its intersection with \(S\) is then nonempty and open in \(S\).

Conversely, suppose \(\mathcal O\cap S\) contains a nonempty open subset \(U\subset S\). Let \(M\) be the union of all \(\preceq\)-maximal orbits in \(Z\). By the previous paragraph, \(M\) is open in \(Z\). It is also dense, because every orbit in the finite closure-order poset of orbits contained in \(Z\) lies below a maximal one. Hence \(M\cap S\) is dense open in \(S\), so \(U\cap M\neq\varnothing\). Any point of \(U\cap M\) lies both in \(\mathcal O\) and in a maximal orbit, and distinct orbits are disjoint. Therefore \(\mathcal O\) itself is maximal.


# lemma lem:signed_chain_model

## statement
Fix \(x_0\in \mathcal O_0\). There is an orthogonal decomposition
\[
V_0\cong \bigoplus_{d\ge 1} M_d\otimes W_d
\]
with the following properties.

1. \(W_d\) is the standard \(d\)-step Jordan chain for \(x_0\): it has basis
\[
w_d,\dots,w_1,\qquad x_0 w_j=w_{j-1},\quad x_0w_1=0.
\]
2. \(M_d\) is the multiplicity space of the rows of length \(d\).
3. The form on \(V_0\) has the factorized shape
\[
\langle m\otimes w,\ m'\otimes w'\rangle
=
\phi_d(m,m')\,\beta_d(w,w'),
\]
where \(\beta_d\) is the standard \(x_0\)-invariant form on \(W_d\), characterized by
\[
\beta_d(w_i,w_j)=0\quad(i+j\neq d+1),\qquad \beta_d(w_d,w_1)=1,
\]
and \(\phi_d\) is nondegenerate.
4. \(\phi_d\) is symmetric exactly for the row lengths on which the real signed diagram carries free sign data:
   - symplectic case: \(d\) even;
   - orthogonal case: \(d\) odd.
   In the complementary parity, \(\phi_d\) is alternating.
5. When \(\phi_d\) is symmetric, its signature records exactly the numbers of \(+\) and \(-\) rows of length \(d\) in \(D_0\). When \(\phi_d\) is alternating, a symplectic basis of \(M_d\) records the corresponding forced \(+/-\) row pairs.

## proof
This is the standard real signed-diagram parametrization rewritten in chain language.

Choose an \(\mathfrak{sl}_2\)-triple containing \(x_0\). The usual isotypic decomposition gives
\[
V_0\cong \bigoplus_{d\ge 1} M_d\otimes W_d
\]
with \(W_d\) the \(d\)-dimensional irreducible \(\mathfrak{sl}_2\)-module. On \(W_d\), fix the standard invariant form \(\beta_d\) normalized by \(\beta_d(w_d,w_1)=1\). Then the ambient form on \(V_0\) splits as \(\phi_d\otimes \beta_d\), with \(\phi_d\) nondegenerate.

The parity of \(\beta_d\) is \((-1)^{d-1}\). Therefore \(\phi_d\) has parity \(\epsilon(-1)^{d-1}\). In the symplectic family \(\epsilon=-1\), so \(\phi_d\) is symmetric exactly when \(d\) is even; in the orthogonal family \(\epsilon=+1\), so \(\phi_d\) is symmetric exactly when \(d\) is odd. This is exactly the free-sign parity from the problem statement.

If \(\phi_d\) is symmetric, choose an orthogonal basis of \(M_d\). The sign of \(\phi_d(m,m)\) is the sign of the corresponding row, because for the chain
\[
m\otimes w_d,\ m\otimes w_{d-1},\ \dots,\ m\otimes w_1
\]
the invariant
\[
\langle m\otimes w_d,\ x_0^{d-1}(m\otimes w_d)\rangle
=
\phi_d(m,m)\,\beta_d(w_d,w_1)
=
\phi_d(m,m)
\]
is exactly the standard signed-diagram row invariant. If \(\phi_d\) is alternating, choose a symplectic basis pair \(e,f\) of \(M_d\) with \(\phi_d(e,f)=1\). Set
\[
z_+:=(e+f)\otimes w_d,\qquad z_-:=(e-f)\otimes w_d.
\]
Then \(z_\pm,x_0z_\pm,\dots,x_0^{d-1}z_\pm\) are two length-\(d\) chains, and
\[
\langle z_\pm,x_0^{d-1}z_\pm\rangle=0,\qquad
\langle z_+,x_0^{d-1}z_-\rangle=-2\,\beta_d(w_d,w_1)\neq 0.
\]
So the multiplicity form on the top space \(\operatorname{span}\{z_+,z_-\}\) is alternating and nondegenerate. By the standard signed-diagram parametrization in the forced-sign parity, such a \(2\)-dimensional block is exactly one forced \(+/-\) pair of rows of length \(d\). Thus every symplectic basis pair of \(M_d\) gives the required forced pair.

Thus the chain model is equivalent to the given signed diagram \(D_0\).


# lemma lem:block_form_for_x0_plus_u

## statement
Choose a totally isotropic complement \(Y\) to \(X\), so that
\[
V=X\oplus V_0\oplus Y
\]
and \(X\) is paired perfectly with \(Y\), while \(V_0\) is orthogonal to \(X\oplus Y\).

Then every element \(x\in x_0+\mathfrak u\) has a unique block form
\[
x=
\begin{pmatrix}
0 & A & C\\
0 & x_0 & B\\
0 & 0 & 0
\end{pmatrix},
\]
meaning
\[
x|_X=0,\qquad
x(v)=x_0v+A(v)\ (v\in V_0),\qquad
x(y)=B(y)+C(y)\ (y\in Y),
\]
where
\[
A=-B^\sharp
\]
with respect to the pairings on \(V_0\) and \(X\leftrightarrow Y\), and \(C:Y\to X\) satisfies
\[
\langle y_1,Cy_2\rangle+\epsilon\,\langle y_2,Cy_1\rangle=0
\qquad(\forall\,y_1,y_2\in Y).
\]

## proof
Because \(x\in x_0+\mathfrak u\), it kills \(X\), induces \(x_0\) on \(V_0=X^\perp/X\), and sends \(Y\) into \(X^\perp=X\oplus V_0\). So \(x\) has the displayed block form for unique linear maps \(A,B,C\).

The relation \(x\in \mathfrak g\) means
\[
\langle xu,v\rangle+\langle u,xv\rangle=0
\qquad(\forall\,u,v\in V).
\]
Taking \(u\in V_0\), \(v\in Y\), and using \(xv=B(v)+C(v)\), \(xu=x_0u+A(u)\), gives
\[
\langle x_0u,v\rangle+\langle A(u),v\rangle+\langle u,B(v)\rangle+\langle u,C(v)\rangle=0.
\]
Here \(\langle x_0u,v\rangle=0\) because \(x_0u\in V_0\) and \(V_0\perp Y\), while \(\langle u,C(v)\rangle=0\) because \(C(v)\in X\) and \(V_0\perp X\). Hence
\[
\langle A(u),v\rangle=-\langle u,B(v)\rangle,
\]
which is exactly \(A=-B^\sharp\).

Taking \(u=y_1\), \(v=y_2\in Y\) gives
\[
\langle y_1,Cy_2\rangle+\epsilon\,\langle y_2,Cy_1\rangle=0,
\]
because \(B(y_i)\in V_0\) and \(V_0\perp Y\). This is the stated condition on \(C\).


# lemma lem:open_in_S_gives_open_in_slice

## statement
Let \(S=\mathcal O_0+\mathfrak u\). If a real nilpotent orbit \(\mathcal O\subset \mathfrak g(\mathbb R)\) satisfies that \(\mathcal O\cap S\) contains a nonempty open subset of \(S\), then \(\mathcal O\cap(x_0+\mathfrak u)\) contains a nonempty open subset of \(x_0+\mathfrak u\).

## proof
Because \(S=G_0\cdot(x_0+\mathfrak u)\), choose a nonempty open subset
\[
W\subset \mathcal O\cap S,
\]
a point \(s\in W\), and \(g\in G_0\) with
\[
g^{-1}s=x_0+n_0\in x_0+\mathfrak u.
\]
Then
\[
W':=g^{-1}W
\]
is again a nonempty open subset of \(\mathcal O\cap S\) and it contains \(x_0+n_0\).

Because \(\mathfrak g_0\cap \mathfrak u=0\), addition gives a homeomorphism
\[
\beta:\mathcal O_0\times \mathfrak u\longrightarrow S,\qquad (x',n)\mapsto x'+n.
\]
Hence \(\beta^{-1}(W')\) is an open subset of \(\mathcal O_0\times \mathfrak u\) containing
\[
(x_0,n_0).
\]
By the definition of the product topology, there are open sets
\[
U_0\subset \mathcal O_0,\qquad N\subset \mathfrak u
\]
with
\[
x_0\in U_0,\qquad n_0\in N,\qquad U_0\times N\subset \beta^{-1}(W').
\]
Since \(x_0\in U_0\), we also have
\[
\{x_0\}\times N\subset U_0\times N\subset \beta^{-1}(W').
\]
Applying \(\beta\), we obtain
\[
x_0+N\subset W'\cap(x_0+\mathfrak u)\subset \mathcal O\cap(x_0+\mathfrak u).
\]
Finally, translation \(n\mapsto x_0+n\) is a homeomorphism \(\mathfrak u\to x_0+\mathfrak u\), so \(x_0+N\) is a nonempty open subset of \(x_0+\mathfrak u\).


# lemma lem:slice_action_map_is_open

## statement
The action map
\[
\alpha:L\times (x_0+\mathfrak u)\longrightarrow S=\mathcal O_0+\mathfrak u,
\qquad
\alpha(l,z)=l\cdot z,
\]
is surjective and open.

## proof
Surjectivity follows from
\[
S=\mathcal O_0+\mathfrak u=G_0\cdot(x_0+\mathfrak u)\subset L\cdot(x_0+\mathfrak u).
\]

Because \(\mathfrak g_0\cap \mathfrak u=0\), addition identifies \(S\) with the product \(\mathcal O_0\times \mathfrak u\):
\[
x'+n\longleftrightarrow (x',n).
\]
Under this identification,
\[
\alpha(l,x_0+n)=\bigl(lx_0l^{-1},\ lnl^{-1}\bigr).
\]
The first component is the orbit map \(L\to \mathcal O_0\), which factors through \(G_0\) and is a smooth submersion because \(\mathcal O_0\) is a homogeneous \(G_0\)-space. The second component is, for fixed \(l\), an invertible linear map \(\mathfrak u\to\mathfrak u\). Hence \(\alpha\) is a smooth submersion. Every smooth submersion is open, so \(\alpha\) is open.


# lemma lem:open_in_slice_gives_open_in_S

## statement
Let
\[
S=\mathcal O_0+\mathfrak u.
\]
If a real nilpotent orbit \(\mathcal O\subset \mathfrak g(\mathbb R)\) satisfies that \(\mathcal O\cap (x_0+\mathfrak u)\) contains a nonempty open subset of \(x_0+\mathfrak u\), then \(\mathcal O\cap S\) contains a nonempty open subset of \(S\).

## proof
Let \(W\subset \mathcal O\cap(x_0+\mathfrak u)\) be nonempty and open in \(x_0+\mathfrak u\).
By lemma `lem:slice_action_map_is_open`, the image
\[
L\cdot W=\alpha(L\times W)
\]
is a nonempty open subset of \(S\).
Because \(\mathcal O\) is \(G\)-stable, it is \(L\)-stable, hence
\[
L\cdot W\subset \mathcal O\cap S.
\]
Therefore \(\mathcal O\cap S\) contains the nonempty open subset \(L\cdot W\).


# lemma lem:symmetric_package_move

## statement
Assume the local package is isolated as follows. Decompose
\[
Y=\mathbb R y\oplus Y_{\mathrm{rest}}
\]
with dual decomposition
\[
X=\mathbb R x_y\oplus X_{\mathrm{rest}},
\qquad
\langle y,x_y\rangle=1.
\]
Let \(d\) be a free-sign length, let
\[
t,\ x_0t,\ \dots,\ x_0^{d-1}t=b
\]
be a length-\(d\) chain whose row sign is \(\sigma\in\{+,-\}\), and suppose
\[
x(y)=t,\qquad x(Y_{\mathrm{rest}})=0.
\]
Then \(y\) generates a row of length \(d+2\), and that row has sign \(-\sigma\).

## proof
Because \(x(y)=t\), we have \(B(y)=t\) and \(C(y)=0\). Also \(x(Y_{\mathrm{rest}})=0\) implies
\[
B(Y_{\mathrm{rest}})=0.
\]

For \(0\le k\le d-2\), put \(u_k:=x_0^k t\). Then
\[
x(u_k)=x_0u_k+A(u_k).
\]
To compute \(A(u_k)\), pair with source vectors. For \(y'\in Y_{\mathrm{rest}}\),
\[
\langle y',A(u_k)\rangle=-\langle B(y'),u_k\rangle=0
\]
because \(B(y')=0\). For the distinguished source vector \(y\),
\[
\langle y,A(u_k)\rangle=-\langle B(y),u_k\rangle=-\langle t,u_k\rangle=0,
\]
since \(u_k\neq b\) and the invariant chain form pairs \(t\) only with the bottom vector \(b=x_0^{d-1}t\). Hence \(A(u_k)\) pairs trivially with all of \(Y\), so \(A(u_k)=0\). Therefore
\[
x^{j}y=x_0^{j-1}t\qquad(1\le j\le d),
\]
and in particular
\[
x^d y=b.
\]

Now compute \(A(b)\). For \(y'\in Y_{\mathrm{rest}}\),
\[
\langle y',A(b)\rangle=-\langle B(y'),b\rangle=0.
\]
For \(y\),
\[
\langle y,A(b)\rangle=-\langle B(y),b\rangle=-\langle t,b\rangle=-\sigma.
\]
Since the pairing \(Y\leftrightarrow X\) is perfect and \(x_y\) is dual to \(y\) while \(A(b)\) pairs trivially with \(Y_{\mathrm{rest}}\), this forces
\[
A(b)=-\sigma\,x_y.
\]
Thus
\[
x^{d+1}y=-\sigma x_y\neq 0,\qquad x^{d+2}y=0.
\]
So \(y\) generates a row of length \(d+2\). Its row invariant is
\[
\langle y,x^{d+1}y\rangle=\langle y,-\sigma x_y\rangle=-\sigma,
\]
so the sign flips.


# lemma lem:alternating_nondegenerate_package_move

## statement
Assume the local package is isolated as follows. Decompose
\[
Y=\mathbb R y\oplus \mathbb R y'\oplus Y_{\mathrm{rest}}
\]
with dual decomposition
\[
X=\mathbb R x_y\oplus \mathbb R x_{y'}\oplus X_{\mathrm{rest}},
\]
where
\[
\langle y,x_y\rangle=\langle y',x_{y'}\rangle=1,\qquad
\langle y,x_{y'}\rangle=\langle y',x_y\rangle=0.
\]
Let \(d\) be a forced-pair length, let \(t_+,t_-\) be the top vectors of one forced \(+/-\) pair of length \(d\), let \(b_+=x_0^{d-1}t_+\), \(b_-=x_0^{d-1}t_-\), and normalize so that
\[
\langle t_+,b_-\rangle=1,\qquad \langle t_-,b_+\rangle=-1,
\]
while all self-pairings vanish. Suppose
\[
x(y)=t_+,\qquad x(y')=t_-,\qquad x(Y_{\mathrm{rest}})=0.
\]
Then \(y\) and \(y'\) generate a forced \(+/-\) pair of rows of length \(d+2\).

## proof
Because \(x(y)=t_+\) and \(x(y')=t_-\), we have
\[
B(y)=t_+,\qquad B(y')=t_-,\qquad C(y)=C(y')=0.
\]
Also \(x(Y_{\mathrm{rest}})=0\) implies \(B(Y_{\mathrm{rest}})=0\).

For \(0\le k\le d-2\), put \(u_k^\pm:=x_0^k t_\pm\). As in the previous lemma,
\[
x(u_k^\pm)=x_0u_k^\pm+A(u_k^\pm).
\]
For \(z\in Y_{\mathrm{rest}}\), \(\langle z,A(u_k^\pm)\rangle=-\langle B(z),u_k^\pm\rangle=0\). For \(y\) and \(y'\), the pairings also vanish because \(u_k^\pm\) is not a bottom vector, so it pairs with neither \(t_+\) nor \(t_-\). Hence \(A(u_k^\pm)=0\). Therefore
\[
x^d y=b_+,\qquad x^d y'=b_-.
\]

Now compute the endpoint corrections. For \(b_+\),
\[
\langle y,A(b_+)\rangle=-\langle t_+,b_+\rangle=0,\qquad
\langle y',A(b_+)\rangle=-\langle t_-,b_+\rangle=1,
\]
and \(A(b_+)\) pairs trivially with \(Y_{\mathrm{rest}}\). Hence
\[
A(b_+)=x_{y'}.
\]
Similarly,
\[
\langle y,A(b_-)\rangle=-\langle t_+,b_-\rangle=-1,\qquad
\langle y',A(b_-)\rangle=-\langle t_-,b_-\rangle=0,
\]
and \(A(b_-)\) pairs trivially with \(Y_{\mathrm{rest}}\), so
\[
A(b_-)=-x_y.
\]
Thus
\[
x^{d+1}y=x_{y'}\neq 0,\qquad x^{d+1}y'=-x_y\neq 0,
\qquad x^{d+2}y=x^{d+2}y'=0.
\]
Hence both \(y\) and \(y'\) generate rows of length \(d+2\).

Their row form on \(\operatorname{span}\{y,y'\}\) is alternating:
\[
\langle y,x^{d+1}y\rangle=\langle y',x^{d+1}y'\rangle=0,
\]
while
\[
\langle y,x^{d+1}y'\rangle=\langle y,-x_y\rangle=-1\neq 0.
\]
So these two rows form one forced \(+/-\) pair of length \(d+2\).


# lemma lem:alternating_radical_package_move

## statement
Assume the local package is isolated as follows. Decompose
\[
Y=\mathbb R y\oplus Y_{\mathrm{rest}}
\]
with dual decomposition
\[
X=\mathbb R x_y\oplus X_{\mathrm{rest}},
\qquad
\langle y,x_y\rangle=1.
\]
Let \(d\) be a forced-pair length, let \(t_+,t_-\) be the top vectors of one forced \(+/-\) pair of length \(d\), let \(b_+=x_0^{d-1}t_+\), \(b_-=x_0^{d-1}t_-\), and normalize so that
\[
\langle t_+,b_-\rangle=1,\qquad \langle t_-,b_+\rangle=-1,
\]
while all self-pairings vanish. Suppose
\[
x(y)=t_+,\qquad x(Y_{\mathrm{rest}})=0.
\]
Then one forced \(+/-\) pair of rows of length \(d\) is replaced by two rows of length \(d+1\) with opposite signs.

## proof
Because \(x(y)=t_+\), we have \(B(y)=t_+\), \(C(y)=0\), and \(B(Y_{\mathrm{rest}})=0\).

For \(0\le k\le d-2\), the same pairing argument as above gives
\[
A(x_0^k t_+)=A(x_0^k t_-)=0.
\]
Hence
\[
x^d y=b_+,\qquad x^{d+1}y=A(b_+)=0,
\]
because \(b_+\) pairs trivially with the only nonzero source image \(t_+\).

For the companion chain \(t_-\in V_0\), we likewise get
\[
x^d t_-=A(b_-).
\]
Now \(A(b_-)\) pairs trivially with \(Y_{\mathrm{rest}}\), and
\[
\langle y,A(b_-)\rangle=-\langle t_+,b_-\rangle=-1.
\]
Therefore
\[
A(b_-)=-x_y,
\qquad\text{so}\qquad
x^d t_-=-x_y,\qquad x^{d+1}t_-=0.
\]

Define
\[
z_+:=t_-+y,\qquad z_-:=t_--y.
\]
Then
\[
x^d z_\pm=-x_y\pm b_+\neq 0,\qquad x^{d+1}z_\pm=0,
\]
so each \(z_\pm\) generates a row of length \(d+1\).

Their row form is
\[
\langle z_+,x^d z_+\rangle
=\langle t_-,b_+\rangle+\langle y,-x_y\rangle
=-1-1=-2,
\]
\[
\langle z_-,x^d z_-\rangle
=-\langle t_-,b_+\rangle+\langle y,x_y\rangle
=1+1=2,
\]
and
\[
\langle z_+,x^d z_-\rangle
=-\langle t_-,b_+\rangle+\langle y,-x_y\rangle
=1-1=0.
\]
So the length-\((d+1)\) multiplicity form on \(\operatorname{span}\{z_+,z_-\}\) is symmetric of split signature \((1,1)\). Therefore one forced \(+/-\) pair of length \(d\) is replaced by two rows of length \(d+1\) with opposite signs.


# lemma lem:zero_row_package_moves

## statement
Suppose
\[
Y=Y'\oplus Y_{\mathrm{rest}}
\]
with dual decomposition
\[
X=X'\oplus X_{\mathrm{rest}},
\]
assume
\[
B|_{Y'}=0,\qquad x(Y')\subset X',\qquad x(Y_{\mathrm{rest}})=0,
\]
and define
\[
c(y,y'):=\langle y,Cy'\rangle.
\]
Then, on the isolated block \(Y'\), the following hold:

1. if \(c\) is nondegenerate, then in the symplectic family the block produces rows of length \(2\) whose signs are given by the signature of \(c\), while in the orthogonal family each symplectic \(2\)-block of \(c\) produces one forced \(+/-\) pair of rows of length \(2\);
2. if \(y\in \operatorname{rad}(c)\), then \(x(y)=0\); in the symplectic family the plane \(\operatorname{span}\{y,x_y\}\) contributes one forced \(+/-\) pair of rows of length \(1\), while in the orthogonal family it splits into two rows of length \(1\) with opposite signs.

## proof
Because \(B|_{Y'}=0\) and \(x(Y')\subset X'\), the restriction of \(x\) to \(Y'\oplus X'\) is square-zero and is determined by \(C|_{Y'}\). The form \(c\) has parity \(-\epsilon\).

If \(\epsilon=-1\), then \(c\) is symmetric. Choose a \(c\)-orthogonal basis \(y_1,\dots,y_k\) with \(c(y_i,y_i)=\sigma_i\in\{+1,-1\}\), and let \(x_{y_i}\in X'\) be characterized by \(\langle y_j,x_{y_i}\rangle=\delta_{ij}\). Then \(x(y_i)=\sigma_i x_{y_i}\) and \(x(x_{y_i})=0\), so each pair \(y_i,x_{y_i}\) is a Jordan chain of length \(2\) with row invariant \(\langle y_i,xy_i\rangle=\sigma_i\). Thus the residual block contributes length-\(2\) rows with signs given by the signature of \(c\).

If \(\epsilon=+1\) and \(c\) is nondegenerate on a \(2\)-dimensional block with symplectic basis \(y,y'\) satisfying \(c(y,y')=1\), then
\[
x(y)=-x_{y'},\qquad x(y')=x_y,
\]
and \(x\) kills \(x_y,x_{y'}\). Hence \(y,y'\) are top vectors of two length-\(2\) chains, and the multiplicity form on \(\operatorname{span}\{y,y'\}\) is alternating and nondegenerate. So the block contributes one forced \(+/-\) pair of rows of length \(2\).
Indeed,
\[
\langle y,xy\rangle=\langle y',xy'\rangle=0,\qquad
\langle y,xy'\rangle=\langle y,x_y\rangle=1,\qquad
\langle y',xy\rangle=\langle y',-x_{y'}\rangle=-1.
\]

Now let \(y\in\operatorname{rad}(c)\). Since \(c(y',y)=0\) for all \(y'\in Y'\), and \(Cy\in X'\), the perfect pairing \(Y'\leftrightarrow X'\) gives \(Cy=0\), hence \(x(y)=0\). Also \(x(x_y)=0\).

If \(\epsilon=-1\), then \(\operatorname{span}\{y,x_y\}\) is a nondegenerate alternating plane on which \(x=0\). By the standard signed-diagram parametrization in the forced-sign parity for \(Sp(2n,\mathbb R)\), this is one forced \(+/-\) pair of length-\(1\) rows.

If \(\epsilon=+1\), set \(z_\pm:=y\pm x_y\). Then
\[
xz_\pm=0,\qquad \langle z_+,z_+\rangle=2,\qquad \langle z_-,z_-\rangle=-2,\qquad \langle z_+,z_-\rangle=0.
\]
So \(z_+\) and \(z_-\) are two length-\(1\) rows with opposite signs.


# proposition prop:local_package_moves

## statement
Keep the block form and the Witt-normal-form reduction from the previous lemmas. Then the contribution of each normal-form package to the signed Young diagram is as follows.

First consider the local normal form in which the chosen source vectors have exactly the displayed images and the remaining source vectors map to \(0\). In that model the package contributions are the following.

1. Symmetric package. Suppose \(d\) is a free-sign length, and \(y\in Y\) maps under \(B\) to a top vector \(t\) of a length-\(d\) chain whose row sign is \(\sigma\in\{+,-\}\). Then the resulting row has length \(d+2\) and row sign \(-\sigma\).
2. Alternating nondegenerate package. Suppose \(d\) is a forced-pair length, and \(y,y'\in Y\) form one nondegenerate symplectic \(2\)-block for \(q_d\). Then the corresponding forced \(+/-\) pair of rows of length \(d\) becomes a forced \(+/-\) pair of rows of length \(d+2\).
3. Alternating radical package. Suppose \(d\) is a forced-pair length and \(y\in Y\) is the one radical direction left at the cutoff. Then one forced \(+/-\) pair of rows of length \(d\) is replaced by two rows of length \(d+1\), and the new sign-carrying pair has opposite signs \(+\) and \(-\).
4. Zero-row nondegenerate package. Suppose \(B=0\) on a block of \(Y\) on which
\[
c(y,y'):=\langle y,Cy'\rangle
\]
is nondegenerate. In the symplectic family this block produces rows of length \(2\) whose signs are given by the signature of \(c\). In the orthogonal family this block produces forced \(+/-\) pairs of rows of length \(2\).
5. Zero-row radical package. Suppose \(B(y)=0\) and \(y\) lies in the radical of \(c\). Then \(x(y)=0\). In the symplectic family the plane \(\operatorname{span}\{y,x_y\}\) contributes one forced \(+/-\) pair of rows of length \(1\). In the orthogonal family the same plane splits into two rows of length \(1\) with opposite signs.

## proof
Item \(1\) is lemma `lem:symmetric_package_move`, item \(2\) is lemma `lem:alternating_nondegenerate_package_move`, item \(3\) is lemma `lem:alternating_radical_package_move`, and items \(4\) and \(5\) are lemma `lem:zero_row_package_moves`.


# lemma lem:highest_degree_form

## statement
Let \(d\ge 1\) be the largest row length occurring in \(D_0\), and write
\[
V_0=(M_d\otimes W_d)\oplus V_{<d}.
\]
For \(x\in x_0+\mathfrak u\) with block map \(B:Y\to V_0\), let
\[
B_d:Y\to M_d
\]
be the projection to the top-of-chain classes in \(M_d\otimes W_d\), and define
\[
q_d(y,y'):=\langle B(y),x_0^{d-1}B(y')\rangle.
\]
Then
\[
q_d(y,y')=\phi_d(B_dy,B_dy').
\]
Hence \(q_d\) depends only on the highest-length top component \(B_d\), and it has the same parity as \(\phi_d\).

## proof
Write
\[
B(y)=B_d(y)\otimes w_d+\ell(y)
\]
with \(\ell(y)\in (M_d\otimes x_0W_d)\oplus V_{<d}\). Since \(d\) is the largest row length, we have
\[
x_0^{d-1}\ell(y')=0.
\]
Therefore
\[
x_0^{d-1}B(y')=B_d(y')\otimes w_1.
\]
Using the factorized form from lemma `lem:signed_chain_model`,
\[
\langle B(y),x_0^{d-1}B(y')\rangle
=
\langle B_d(y)\otimes w_d,\ B_d(y')\otimes w_1\rangle
=
\phi_d(B_dy,B_dy')\,\beta_d(w_d,w_1),
\]
and \(\beta_d(w_d,w_1)=1\). This gives the formula.


# lemma lem:highest_degree_tail_clearing

## statement
Keep the notation of lemma `lem:highest_degree_form`, and let \(Y_d\subset Y\) be an \(m\)-dimensional subspace on which \(B_d\) is injective.
Then there is \(g\in Z_{G_0}(x_0)\) such that:

1. the degree-\(d\) top component \(B_d|_{Y_d}\) is unchanged after conjugating \(x\) by \((1,g)\in GL(X)\times G_0\);
2. the new block map satisfies
\[
B(Y_d)\subset M_d\otimes W_d .
\]

## proof
For \(e<d\) and \(0\le j\le e-1\), put
\[
F_e^j:=M_e\otimes x_0^jW_e.
\]
We kill the \(e\)-block of \(B(Y_d)\) one quotient \(F_e^j/F_e^{j+1}\) at a time.

Assume that the current \(e\)-component of \(B(Y_d)\) lies in \(F_e^j\). Let
\[
\overline r_j:Y_d\longrightarrow F_e^j/F_e^{j+1}
\]
be the resulting quotient map. Because \(B_d|_{Y_d}:Y_d\to \operatorname{im}(B_d|_{Y_d})\) is an isomorphism, there is a unique linear map
\[
\overline u_j:\operatorname{im}(B_d|_{Y_d})\longrightarrow F_e^j/F_e^{j+1}
\]
such that
\[
\overline u_j(B_dy)=-\overline r_j(y)\qquad (y\in Y_d).
\]
The chosen chain basis on \(W_e\) gives the canonical splitting
\[
F_e^j=(M_e\otimes \mathbb R x_0^jw_e)\oplus F_e^{j+1},
\]
so \(\overline u_j\) has a unique lift
\[
u_j:\operatorname{im}(B_d|_{Y_d})\longrightarrow M_e\otimes \mathbb R x_0^jw_e\subset F_e^j.
\]
Extend \(u_j\) by \(0\) on a complement of \(\operatorname{im}(B_d|_{Y_d})\) in \(M_d\). Put
\[
D:=M_d\otimes W_d,\qquad E:=M_e\otimes W_e,
\]
let \(U_j:D\to E\) be the \(\mathbb R[t]\)-map determined by
\[
U_j(m\otimes w_d)=u_j(m),
\]
extend it by \(0\) on every other Jordan block, and set
\[
N_j:=U_j-U_j^\dagger,\qquad g_j:=\exp(N_j).
\]

Because \(U_j\) commutes with \(x_0\), so does \(U_j^\dagger\); because \(N_j\) is skew-adjoint, \(g_j\in Z_{G_0}(x_0)\). Also \(x_0^e=0\) on \(E\), so
\[
x_0^eU_j^\dagger=U_j^\dagger x_0^e=0.
\]
Hence
\[
\operatorname{im}(U_j^\dagger)\subset \ker(x_0^e)\cap D=M_d\otimes x_0^{d-e}W_d.
\]
Since \(U_j^\dagger\) commutes with \(x_0\), this improves on \(F_e^j=x_0^jE\) to
\[
U_j^\dagger(F_e^j)\subset M_d\otimes x_0^{d-e+j}W_d\subset x_0D.
\]
Therefore \(g_j\) induces the identity on \(D/x_0D\), so the degree-\(d\) top class \(B_d|_{Y_d}\) is unchanged.

Now fix \(y\in Y_d\). Decompose the current \(D\oplus E\)-part of \(B(y)\) as
\[
d(y)+r_e(y),
\]
where \(d(y)\in D\) and \(r_e(y)\in F_e^j\); write the sum of all blocks of lengths \(<e\) as \(r_{<e}(y)\). Because \(U_j\) and \(U_j^\dagger\) vanish on every block other than \(D\oplus E\), the \(e\)-component of \(g_jB(y)\) is the \(E\)-component of \(\exp(N_j)(d(y)+r_e(y))\).

Relative to \(D\oplus E\),
\[
N_j=
\begin{pmatrix}
0 & -U_j^\dagger\\
U_j & 0
\end{pmatrix}.
\]
Since \(U_j^2=(U_j^\dagger)^2=0\), an induction gives
\[
N_j^{2k}=
\begin{pmatrix}
(-U_j^\dagger U_j)^k & 0\\
0 & (-U_jU_j^\dagger)^k
\end{pmatrix},
\qquad
N_j^{2k+1}=
\begin{pmatrix}
0 & *\\
(-U_jU_j^\dagger)^kU_j & 0
\end{pmatrix}.
\]
Hence the \(e\)-component of \(\exp(N_j)(d(y)+r_e(y))\) is
\[
r_e(y)+U_jd(y)
+\sum_{k\ge 1}\frac{(-1)^k}{(2k)!}(U_jU_j^\dagger)^k r_e(y)
+\sum_{k\ge 1}\frac{(-1)^k}{(2k+1)!}(U_jU_j^\dagger)^k U_jd(y).
\]

Write
\[
d(y)=B_d(y)\otimes w_d+d_1(y)
\]
with \(d_1(y)\in x_0D\). Because \(U_j\) is \(\mathbb R[t]\)-linear and \(\operatorname{im}(U_j)\subset F_e^j\), we have
\[
U_jd_1(y)\subset x_0F_e^j\subset F_e^{j+1}.
\]
So
\[
U_jd(y)\equiv u_j(B_dy)\pmod{F_e^{j+1}}.
\]
By construction of \(u_j\),
\[
r_e(y)+u_j(B_dy)\in F_e^{j+1}.
\]

For every term of order at least \(2\), the displayed expansion contains a factor \(U_jU_j^\dagger\). Since \(r_e(y)\in F_e^j\) and \(U_jd(y)\in F_e^j\), it is enough to note that
\[
U_jU_j^\dagger(F_e^j)\subset U_j\!\bigl(M_d\otimes x_0^{d-e+j}W_d\bigr)
\subset x_0^{d-e+j}F_e^j\subset F_e^{j+1},
\]
because \(d-e+j\ge 1\). Thus every higher-order contribution to the \(e\)-component lies in \(F_e^{j+1}\). Therefore the new \(e\)-component of \(g_jB(y)\) lies in \(F_e^{j+1}\), while \(B_d(y)\) is unchanged.

Starting with \(j=0\) and increasing to \(j=e-1\), we push the whole \(e\)-block to \(0\). The step indexed by \(j'\) has image in \(F_e^{j'}\), so it cannot recreate any previously cleared quotient \(F_e^j/F_e^{j+1}\) with \(j<j'\). Now run this procedure for \(e=d-1,d-2,\dots,1\). A later step for a shorter length \(e'<e\) acts only on \(D\oplus(M_{e'}\otimes W_{e'})\), so it is the identity on the already cleared \(e\)-block. Multiplying the resulting \(g_j\)'s in that order gives \(g\in Z_{G_0}(x_0)\) such that \(B_d|_{Y_d}\) is fixed and
\[
B(Y_d)\subset M_d\otimes W_d.
\]


# lemma lem:highest_degree_power_formula

## statement
For every \(y\in Y\) and every \(k\ge 1\),
\[
x^{k+1}y=x_0^kB(y)+A\!\bigl(x_0^{k-1}B(y)\bigr).
\]
In particular, if \(d\) is the largest row length in \(D_0\), then
\[
x^{d+1}y=A\!\bigl(x_0^{d-1}B(y)\bigr),\qquad x^{d+2}y=0.
\]

## proof
Because \(y\in Y\), we have
\[
xy=B(y)+C(y)
\]
with \(C(y)\in X\). Since \(x\) kills \(X\), applying \(x\) once gives
\[
x^2y=x_0B(y)+A(B(y)).
\]
This is the case \(k=1\).

Assume now
\[
x^{k+1}y=x_0^kB(y)+A\!\bigl(x_0^{k-1}B(y)\bigr).
\]
Applying \(x\) and using again that \(x\) kills \(X\), we get
\[
x^{k+2}y
=x\!\bigl(x_0^kB(y)\bigr)
=x_0^{k+1}B(y)+A\!\bigl(x_0^kB(y)\bigr).
\]
So the formula holds for all \(k\ge 1\). Since all Jordan blocks of \(x_0\) have length at most \(d\), we have \(x_0^dB(y)=0\), which gives the two displayed consequences.


# lemma lem:highest_degree_general_package_moves

## statement
Keep the notation of lemma `lem:highest_degree_form`, and assume
\[
B(Y_d)\subset M_d\otimes W_d,
\qquad
Y=Y_d\oplus \ker(B_d),
\]
so \(B(y)=B_d(y)\otimes w_d\) for \(y\in Y_d\), and
\(B_d|_{Y_d}:Y_d\to \operatorname{im}(B_d)\) is an isomorphism.
Suppose \(q_d\) is in Witt normal form on \(Y_d\). Then the highest-degree contribution of each Witt block is:

1. a nondegenerate symmetric Witt line with value \(\sigma\in\{+1,-1\}\) gives one row of length \(d+2\) and sign \(-\sigma\);
2. a symmetric radical line gives one forced \(+/-\) pair of rows of length \(d+1\);
3. an alternating Witt \(2\)-block gives one forced \(+/-\) pair of rows of length \(d+2\);
4. an alternating radical line gives two rows of length \(d+1\) with opposite signs.

## proof
Choose a Witt basis of \(Y_d\), extend it to a basis of \(Y\) by a basis of \(\ker(B_d)\), and let
\[
\{x_{y'}\}\cup \{x_z\}
\]
be the dual basis of \(X\), where \(y'\) runs over the chosen Witt basis of \(Y_d\) and \(z\) runs over the chosen basis of \(\ker(B_d)\). Write
\[
b(y):=x_0^{d-1}B(y)=B_d(y)\otimes w_1\qquad (y\in Y_d).
\]
For every \(y'\in Y_d\), lemma `lem:highest_degree_form` gives
\[
\langle B(y'),b(y)\rangle=q_d(y',y).
\]
Because the ambient form on \(V_0\) has parity \(\epsilon\), this implies
\[
\langle b(y),B(y')\rangle=\epsilon\,q_d(y',y).
\]
For every \(z\in \ker(B_d)\), lemma `lem:highest_degree_form` gives
\[
\langle B(z),b(y)\rangle=q_d(z,y)=\phi_d(B_dz,B_dy)=0,
\]
because \(B_dz=0\). Therefore the characterization \(A=-B^\sharp\) from lemma `lem:block_form_for_x0_plus_u` yields
\[
\langle y',A(b(y))\rangle
=
\epsilon\langle A(b(y)),y'\rangle
=
-\epsilon\langle b(y),B(y')\rangle
=
-q_d(y',y),
\]
\[
\langle z,A(b(y))\rangle=0.
\]
Hence lemma `lem:highest_degree_power_formula` gives
\[
x^{d+1}y=A(b(y))=-\sum_{y'} q_d(y',y)\,x_{y'}.
\]

For a nondegenerate symmetric Witt line \(y\), \(q_d(y,y)=\sigma\), so
\[
x^{d+1}y=-\sigma x_y\neq 0.
\]
Since lemma `lem:highest_degree_power_formula` also gives \(x^{d+2}y=0\), this is one length-\((d+2)\) row, and
\[
\langle y,x^{d+1}y\rangle=-\sigma.
\]

Now let \(y\) span a symmetric radical line of \(q_d\), and let \(U\) be the span of the remaining Witt blocks in \(B_d(Y_d)\), so
\[
B_d(Y_d)=\mathbb R\,B_dy\oplus U.
\]
Because \(q_d\) is symmetric here, \(\phi_d\) is symmetric. Choose \(t_-\in M_d\) so that
\[
\phi_d(B_dy,t_-)=1,\qquad \phi_d(U,t_-)=0,
\]
and put \(v_-:=t_-\otimes w_d\). Since \(y\) is radical, \(q_d(y',y)=0\) for every \(y'\in Y_d\), so the displayed formula gives \(x^{d+1}y=0\). Also \(x_0^{d-1}v_-\) pairs trivially with every \(B(z)\) for \(z\in\ker(B_d)\), and with every \(B(y')\) except \(B(y)\); therefore
\[
A(x_0^{d-1}v_-)=-x_y.
\]
The same induction as in lemma `lem:highest_degree_power_formula` then gives
\[
x^dv_-=A(x_0^{d-1}v_-)=-x_y,\qquad x^{d+1}v_-=0.
\]
Write
\[
x^dy=b(y)+\eta,
\]
where
\[
\eta=
\begin{cases}
A(B_d(y)\otimes w_2), & d>1,\\
C(y), & d=1.
\end{cases}
\]
If \(d>1\), then
\[
\langle y,\eta\rangle=-\langle B(y),B_d(y)\otimes w_2\rangle=0
\]
because \(\beta_d(w_d,w_2)=0\). If \(d=1\), then we are in the orthogonal family, so \(\epsilon=+1\), and the skew condition on \(C\) gives \(\langle y,C(y)\rangle=0\). Therefore the top-space form
\[
g(u,v):=\langle u,x^dv\rangle
\]
on \(T:=\operatorname{span}\{v_-,y\}\) has matrix
\[
\begin{pmatrix}
0 & 1\\
-1 & 0
\end{pmatrix}
\]
in the basis \((v_-,y)\). Since \(d\) had free-sign parity, \(d+1\) has forced-pair parity, and this alternating nondegenerate form is exactly one forced \(+/-\) pair of rows of length \(d+1\).

For an alternating Witt block \(y,y'\), choose the Witt basis so that \(q_d(y,y')=1\), \(q_d(y',y)=-1\), and the self-pairings vanish. Then
\[
x^{d+1}y=x_{y'},\qquad x^{d+1}y'=-x_y.
\]
Hence both vectors generate length-\((d+2)\) chains, their self-pairings vanish, and
\[
\langle y,x^{d+1}y'\rangle=-1\neq 0,
\]
so they form one forced \(+/-\) pair.

Finally, let \(y\) span a radical line of \(q_d\), and let \(U\) be the span of the remaining Witt blocks in \(B_d(Y_d)\), so
\[
B_d(Y_d)=\mathbb R\,B_dy\oplus U.
\]
Because \(y\) is radical, \(q_d(y',y)=0\) for every \(y'\in Y_d\), hence \(B_dy\) is \(\phi_d\)-orthogonal to all of \(U\). Choose \(t_-\in M_d\) so that
\[
\phi_d(B_dy,t_-)=1,\qquad \phi_d(U,t_-)=0,
\]
and put \(v_-:=t_-\otimes w_d\). This is possible because the nondegeneracy of \(\phi_d\) identifies \(M_d\) with \(M_d^\ast\): the linear functional on \(B_d(Y_d)\) sending \(B_dy\mapsto 1\) and \(U\mapsto 0\) is represented by some \(t_-\in M_d\). Then \(q_d(y',y)=0\) for every \(y'\in Y_d\), and for every \(z\in \ker(B_d)\) we also have
\[
q_d(z,y)=\phi_d(B_dz,B_dy)=0.
\]
So \(A(b(y))=0\). Also \(x_0^{d-1}v_-\) pairs trivially with every \(B(y')\) except \(B(y)\): it is orthogonal to \(V_{<d}\), and on the degree-\(d\) top space the above choice of \(t_-\) kills \(U\). Hence \(A(x_0^{d-1}v_-)=-x_y\).

For the source vector \(y\), lemma `lem:highest_degree_power_formula` gives
\[
x^dy=b(y)+\eta,\qquad x^{d+1}y=0
\]
with
\[
\eta=
\begin{cases}
A(B_d(y)\otimes w_2), & d>1,\\
C(y), & d=1.
\end{cases}
\]
If \(d>1\), then
\[
\langle y,\eta\rangle=-\langle B(y),B_d(y)\otimes w_2\rangle=0.
\]
For \(d=1\), write
\[
c(y,y):=\langle y,C(y)\rangle.
\]
For the vector \(v_-\in V_0\), the same induction used in lemma `lem:highest_degree_power_formula` gives
\[
x^kv_-=x_0^kv_-+A(x_0^{k-1}v_-)\qquad (k\ge 1),
\]
hence
\[
x^dv_-=-x_y,\qquad x^{d+1}v_-=0.
\]
Setting \(z_\pm:=v_-\pm y\), we obtain
\[
x^dz_\pm=-x_y\pm b(y)+\eta,\qquad x^{d+1}z_\pm=0.
\]
If \(d>1\), then \(\phi_d\) is alternating, so
\[
\langle v_-,b(y)\rangle=\phi_d(t_-,B_dy)=-1.
\]
Since \(V_0\perp X\) and \(\langle y,\eta\rangle=0\), the \(\eta\)-term does not affect the row form. Therefore
\[
\langle z_+,x^dz_+\rangle=-2,\qquad
\langle z_-,x^dz_-\rangle=2,\qquad
\langle z_+,x^dz_-\rangle=0.
\]
So \(z_+\) and \(z_-\) give two length-\((d+1)\) rows with opposite signs.

If \(d=1\), then the top-space form
\[
g(u,v):=\langle u,xv\rangle
\]
on \(T:=\operatorname{span}\{v_-,y\}\) has matrix
\[
\begin{pmatrix}
0 & -1\\
-1 & c(y,y)
\end{pmatrix}
\]
in the basis \((v_-,y)\). Its determinant is \(-1\), so it is symmetric nondegenerate of signature \((1,1)\). Hence \(T\) again contributes two rows of length \(2=d+1\) with opposite signs.


# lemma lem:package_complement

## statement
Let
\[
Y=Y_U\oplus Y_{\mathrm{res}}
\]
and let
\[
X=X_U\oplus X_{\mathrm{res}}
\]
be the dual decomposition. Suppose \(U\subset V\) is a nondegenerate \(x\)-stable subspace such that
\[
X_U\oplus Y_U\subset U,
\qquad
U\perp (X_{\mathrm{res}}\oplus Y_{\mathrm{res}}).
\]
Then \(U^\perp\) is \(x\)-stable, contains \(X_{\mathrm{res}}\oplus Y_{\mathrm{res}}\), and admits the direct-sum decomposition
\[
U^\perp=X_{\mathrm{res}}\oplus (U^\perp\cap V_0)\oplus Y_{\mathrm{res}}.
\]
This is a Witt-type decomposition: \(U^\perp\cap V_0\) is orthogonal to
\[
X_{\mathrm{res}}\oplus Y_{\mathrm{res}},
\]
and \(X_{\mathrm{res}}\) is paired perfectly with \(Y_{\mathrm{res}}\).
Consequently \(x|_{U^\perp}\) again has the block form of lemma `lem:block_form_for_x0_plus_u` for the smaller isotropic subspace \(X_{\mathrm{res}}\), so the residual source dimension is \(r-\dim Y_U\).

## proof
For \(u\in U\) and \(v\in U^\perp\),
\[
\langle u,xv\rangle=-\langle xu,v\rangle.
\]
Because \(U\) is \(x\)-stable, \(xu\in U\), so the right side is \(0\). Thus \(xv\in U^\perp\), proving \(x\)-stability.

Because \(U\perp (X_{\mathrm{res}}\oplus Y_{\mathrm{res}})\), we already have
\[
X_{\mathrm{res}}\oplus Y_{\mathrm{res}}\subset U^\perp.
\]
Now take \(v\in U^\perp\), and write it in the fixed decomposition \(V=X\oplus V_0\oplus Y\) as
\[
v=x_U+x_{\mathrm{res}}+v_0+y_U+y_{\mathrm{res}}.
\]
Since \(Y_U\subset U\) and \(v\perp Y_U\), the perfect pairing \(X\leftrightarrow Y\) forces \(x_U=0\). Likewise \(X_U\subset U\) and \(v\perp X_U\) force \(y_U=0\). Therefore
\[
v=x_{\mathrm{res}}+v_0+y_{\mathrm{res}},
\]
so
\[
U^\perp=X_{\mathrm{res}}\oplus (U^\perp\cap V_0)\oplus Y_{\mathrm{res}}.
\]
Because \(V_0\perp (X\oplus Y)\), the middle summand \(U^\perp\cap V_0\) is orthogonal to
\[
X_{\mathrm{res}}\oplus Y_{\mathrm{res}}.
\]
By the dual decompositions of \(X\) and \(Y\), the ambient pairing restricts to a perfect pairing
\[
X_{\mathrm{res}}\times Y_{\mathrm{res}}\longrightarrow \mathbb R.
\]
The space \(X_{\mathrm{res}}\) is totally isotropic and has dimension
\[
r-\dim Y_U.
\]
Because \(x\) kills \(X\), we have \(x|_{X_{\mathrm{res}}}=0\). If \(y\in Y_{\mathrm{res}}\), then the original block form of lemma `lem:block_form_for_x0_plus_u` gives
\[
x y\in X\oplus V_0.
\]
Because \(xy\in U^\perp\) as well, its decomposition in
\[
U^\perp=X_{\mathrm{res}}\oplus (U^\perp\cap V_0)\oplus Y_{\mathrm{res}}
\]
has no \(Y_{\mathrm{res}}\)-component, so
\[
C(Y_{\mathrm{res}})\subset X_{\mathrm{res}},
\qquad
B(Y_{\mathrm{res}})\subset U^\perp\cap V_0.
\]
Likewise, if \(v_0\in U^\perp\cap V_0\), then the original block form gives
\[
xv_0=x_0v_0+A(v_0)\in V_0\oplus X.
\]
Again \(xv_0\in U^\perp\), so its \(X\)-component lies in \(X_{\mathrm{res}}\), and its \(V_0\)-component lies in \(U^\perp\cap V_0\). Therefore
\[
x_0(U^\perp\cap V_0)\subset U^\perp\cap V_0,
\qquad
A(U^\perp\cap V_0)\subset X_{\mathrm{res}}.
\]
Finally, \(U\) is nondegenerate, so \(U^\perp\) is nondegenerate. The paired subspace
\[
X_{\mathrm{res}}\oplus Y_{\mathrm{res}}
\]
is nondegenerate inside \(U^\perp\) because \(X_{\mathrm{res}}\) is paired perfectly with \(Y_{\mathrm{res}}\). Since \(U^\perp\cap V_0\) is orthogonal to that paired subspace and the direct sum above exhausts \(U^\perp\), it is exactly the orthogonal complement of \(X_{\mathrm{res}}\oplus Y_{\mathrm{res}}\) inside \(U^\perp\); in particular it is nondegenerate. Thus, relative to the decomposition
\[
U^\perp=X_{\mathrm{res}}\oplus (U^\perp\cap V_0)\oplus Y_{\mathrm{res}},
\]
the restriction \(x|_{U^\perp}\) again has the same upper-triangular block form as in lemma `lem:block_form_for_x0_plus_u`, with source \(Y_{\mathrm{res}}\) dual to \(X_{\mathrm{res}}\).


# lemma lem:package_subspace_endpoints

## statement
Keep the notation of lemma `lem:highest_degree_general_package_moves`, and put
\[
m:=\dim Y_d.
\]
Assume
\[
Y=Y_d\oplus \ker(B_d),
\qquad
B(Y_d)\subset M_d\otimes W_d,
\]
\[
B_d|_{Y_d}:Y_d\to \operatorname{im}(B_d)
\quad\text{an isomorphism,}
\]
and assume that \(q_d|_{Y_d}\) already has the maximal-rank shape forced later by lemma `lem:maximal_partition_forces_maximal_highest_degree_package`: in the free-sign case it is nondegenerate symmetric, and in the forced-pair case it has rank \(m\) if \(m\) is even and rank \(m-1\) if \(m\) is odd. Define \(U\subset V\) by taking the \(x\)-stable hull of the first-step package generators:

1. in the free-sign case,
\[
U:=\sum_{i=1}^m \mathbb R[x]\,y_i
\]
for an orthogonal basis \(y_1,\dots,y_m\) of \(Y_d\);
2. in the forced-pair case with \(m=2a\),
\[
U:=\sum_{i=1}^a \bigl(\mathbb R[x]\,y_i+\mathbb R[x]\,y_i'\bigr)
\]
for a symplectic basis \((y_1,y_1'),\dots,(y_a,y_a')\) of \(Y_d\);
3. in the forced-pair case with \(m=2a+1\), use such a symplectic basis for the rank-\(2a\) part, let \(y_0\) span the radical line, choose the companion vector \(v_-\) from lemma `lem:highest_degree_general_package_moves`, and set
\[
U:=\mathbb R[x]\,y_0+\mathbb R[x]\,v_-+\sum_{i=1}^a \bigl(\mathbb R[x]\,y_i+\mathbb R[x]\,y_i'\bigr).
\]

Then \(Y_d\subset U\). If \(X_U\subset X\) is the span of the deepest package endpoints
\[
x^{d+1}y_i,\quad x^{d+1}y_i',\quad x^d v_-\ \text{(when present)},
\]
then
\[
U\cap X=X_U,
\]
and the pairing \(Y_d\times X_U\to\mathbb R\) is perfect. In particular,
\[
\dim X_U=m.
\]

## proof
By construction \(Y_d\subset U\). Lemma `lem:highest_degree_general_package_moves` gives the deepest endpoints:
\[
x^{d+1}y_i=-\sigma_i x_{y_i},\qquad
x^{d+1}y_i=x_{y_i'},\quad x^{d+1}y_i'=-x_{y_i},\qquad
x^d v_-=-x_{y_0},
\]
in the free-sign, even forced-pair, and odd forced-pair cases respectively. These vectors lie in \(U\cap X\) and pair perfectly with the chosen basis of \(Y_d\), so
\[
\dim X_U=m.
\]

Let \(y_\bullet\) denote the chosen basis of \(Y_d\): in the free-sign case \(y_1,\dots,y_m\), in the even forced-pair case \(y_1,y_1',\dots,y_a,y_a'\), and in the odd forced-pair case \(y_0,y_1,y_1',\dots,y_a,y_a'\).

Let \(W\) be the span of all package generators except these deepest endpoints. Thus \(U=W+X_U\), so it is enough to prove
\[
W\cap X=0.
\]

Take \(u\in W\cap X\). In the fixed decomposition
\[
V=X\oplus V_0\oplus Y,
\]
the only vectors in \(W\) with nonzero \(Y\)-component are the source vectors in \(Y_d\). Since \(u\in X\) has no \(Y\)-component, all source-vector coefficients in \(u\) vanish. Therefore \(u\) is a linear combination of
\[
x^k y_\bullet\qquad (1\le k\le d)
\]
and, in the odd forced-pair case, also
\[
x^k v_-\qquad (0\le k\le d-1).
\]
All these vectors lie in \(X^\perp\), so we may apply the quotient map
\[
\pi:X^\perp\longrightarrow V_0=X^\perp/X.
\]

For \(y\in Y_d\), the block form gives
\[
\pi(xy)=B(y)=B_d(y)\otimes w_d.
\]
For \(2\le k\le d\), lemma `lem:highest_degree_power_formula` gives
\[
x^k y=x_0^{k-1}B(y)+A(x_0^{k-2}B(y)),
\]
and the \(A\)-term lies in \(X\). Because \(B(y)=B_d(y)\otimes w_d\), we obtain
\[
\pi(x^k y)=x_0^{k-1}B(y)=B_d(y)\otimes w_{d-k+1}\neq 0
\qquad (1\le k\le d).
\]

In the odd forced-pair case, \(v_-\in V_0\), so \(xv_-=x_0v_-+A(v_-)\) with \(A(v_-)\in X\). Inductively,
\[
\pi(x^k v_-)=x_0^k v_-=t_-\otimes w_{d-k}\neq 0
\qquad (0\le k\le d-1).
\]

Now put
\[
E:=\operatorname{im}(B_d)\subset M_d.
\]
Because \(B_d|_{Y_d}\) is an isomorphism, the chosen basis of \(Y_d\) maps to a basis of \(E\). Hence the vectors
\[
B_d(y_\bullet)\otimes w_j
\]
coming from the source chains form a basis of
\[
E\otimes W_d,
\]
so they are linearly independent.

In the odd forced-pair case, write
\[
E=\mathbb R\,B_dy_0\oplus U_0,
\]
where \(U_0\) is the symplectic part of the Witt decomposition. By construction,
\[
\phi_d(B_dy_0,t_-)=1,\qquad \phi_d(U_0,t_-)=0.
\]
If \(t_-\in E\), then \(t_-=\alpha B_dy_0+u_0\) with \(u_0\in U_0\). Since \(y_0\) spans the radical line of \(q_d|_{Y_d}\), the vector \(B_dy_0\) is \(\phi_d\)-orthogonal to all of \(E\), so
\[
\phi_d(B_dy_0,t_-)=0,
\]
contrary to the displayed normalization. Thus
\[
t_-\notin E.
\]
Therefore the companion classes \(t_-\otimes w_j\) are linearly independent from \(E\otimes W_d\).

So every non-endpoint vector spanning \(W\) has a \(V_0\)-class, and all those classes are linearly independent. Since \(u\in X\), we have \(\pi(u)=0\). Hence every coefficient of those non-endpoint vectors is zero, so \(u=0\). This proves \(W\cap X=0\).

Finally, if \(u\in U\cap X\), write \(u=w+x_*\) with \(w\in W\) and \(x_*\in X_U\). Then
\[
w=u-x_*\in W\cap X=0,
\]
so \(u=x_*\in X_U\). Therefore
\[
U\cap X=X_U.
\]


# lemma lem:package_subspace_middle_projection

## statement
Keep the notation of lemma `lem:package_subspace_endpoints`. Then the image of
\[
U\cap X^\perp
\]
in
\[
V_0=X^\perp/X
\]
is exactly the \(x_0\)-stable subspace
\[
D_U:=\sum_{y\in Y_d}\mathbb R[x_0]\,B(y)
\]
in the free-sign and even forced-pair cases, and
\[
D_U:=\mathbb R[x_0]\,v_-+\sum_{y\in Y_d}\mathbb R[x_0]\,B(y)
\]
in the odd forced-pair case. Its kernel is \(X_U\). Moreover \(U\) is nondegenerate, \(x\)-stable, and its signed Young diagram is exactly the first recursive move determined by \(q_d|_{Y_d}\).

## proof
By construction \(U\) is \(x\)-stable. As in the proof of lemma
`lem:package_subspace_endpoints`, the only generators of \(U\) with nonzero
\(Y\)-component are the source vectors in \(Y_d\). Therefore
\(U\cap X^\perp\) is generated by \(X_U\), by
\[
x^k y\qquad (y\in Y_d,\ 1\le k\le d),
\]
and, in the odd forced-pair case, by
\[
x^k v_-\qquad (0\le k\le d-1).
\]
Under the quotient map
\[
\pi:X^\perp\to V_0=X^\perp/X,
\]
these generators map to
\[
B(y),\ x_0B(y),\dots,x_0^{d-1}B(y)
\]
for \(y\in Y_d\), and in the odd forced-pair case also to
\[
v_-,\ x_0v_-,\dots,x_0^{d-1}v_-.
\]
Hence the image of \(U\cap X^\perp\) is exactly \(D_U\). Lemma
`lem:package_subspace_endpoints` gives
\[
(U\cap X^\perp)\cap X=U\cap X=X_U,
\]
so the kernel of \(\pi|_{U\cap X^\perp}\) is \(X_U\). Thus
\[
(U\cap X^\perp)/X_U\cong D_U.
\]

The quotient \(U/(U\cap X^\perp)\) is represented by the \(m\) source vectors in \(Y_d\), so
\[
\dim U=\dim Y_d+\dim D_U+\dim X_U.
\]
In the free-sign and even forced-pair cases, \(D_U\) is the direct sum of \(m\) length-\(d\) \(x_0\)-chains, so
\[
\dim D_U=md.
\]
Thus
\[
\dim U=m+md+m=m(d+2).
\]
If \(m=2a+1\), then \(D_U\) is the direct sum of the \(2a+1\) length-\(d\) chains coming from \(Y_d\) together with the extra length-\(d\) companion chain generated by \(v_-\), so
\[
\dim D_U=(2a+2)d.
\]
Hence
\[
\dim U=(2a+1)+(2a+2)d+(2a+1)=2a(d+2)+2(d+1).
\]

Lemma `lem:highest_degree_general_package_moves` constructs inside \(U\) exactly the rows of the first recursive move: \(m\) rows of length \(d+2\) in the free-sign case, \(\frac m2\) forced pairs of length \(d+2\) when \(m\) is even, and \(a\) forced pairs of length \(d+2\) together with one forced pair of length \(d+1\) when \(m=2a+1\). The total dimension of those rows is exactly the dimension computed above, so they span all of \(U\). Those rows form a nondegenerate \(x\)-stable classical block, hence \(U\) is nondegenerate and its signed Young diagram is exactly the first recursive move.


# proposition prop:highest_degree_reduction

## statement
Keep the notation of lemmas `lem:package_subspace_endpoints` and `lem:package_subspace_middle_projection`. Let
\[
X_{\mathrm{res}}:=\{x\in X:\langle y,x\rangle=0\ \text{for all }y\in Y_d\}.
\]
Then
\[
X=X_U\oplus X_{\mathrm{res}}.
\]
Choose a totally isotropic complement
\[
Y_{\mathrm{res}}\subset U^\perp
\]
to \(X_{\mathrm{res}}\) inside the nondegenerate space \(U^\perp\), and put
\[
Y':=Y_d\oplus Y_{\mathrm{res}}.
\]
Then \(Y'\) is a totally isotropic complement to \(X\). Relative to this new complement, the restriction \(x|_{U^\perp}\) lies in a smaller slice
\[
x_{0,\mathrm{res}}+\mathfrak u_{\mathrm{res}}
\]
with source dimension \(r-m\), and the signed Young diagram of \(x_{0,\mathrm{res}}\) is obtained from \(D_0\) by deleting the rows consumed in the first recursive move; equivalently, its chain model is
\[
D_{\mathrm{res}}=D_U^\perp.
\]

## proof
Lemma `lem:package_subspace_endpoints` gives a perfect pairing
\[
Y_d\times X_U\longrightarrow \mathbb R.
\]
Hence the annihilator of \(Y_d\) inside \(X\) has codimension \(\dim X_U\), so
\[
X=X_U\oplus X_{\mathrm{res}}.
\]

We next prove
\[
X_{\mathrm{res}}\subset U^\perp.
\]
Indeed, \(U\) is generated by \(Y_d\), by \(U\cap X^\perp\), and by \(X_U\). By definition, \(X_{\mathrm{res}}\) annihilates \(Y_d\). Also \(X_{\mathrm{res}}\subset X\) is orthogonal to \(X_U\) because \(X\) is totally isotropic, and \(X_{\mathrm{res}}\) is orthogonal to \(U\cap X^\perp\) because \(U\cap X^\perp\subset X^\perp\). So \(X_{\mathrm{res}}\) is orthogonal to every generator of \(U\), proving the claim.

Since \(U\) is nondegenerate by lemma `lem:package_subspace_middle_projection`, \(U^\perp\) is nondegenerate. Because \(X_{\mathrm{res}}\subset U^\perp\) is totally isotropic, a totally isotropic complement \(Y_{\mathrm{res}}\subset U^\perp\) to \(X_{\mathrm{res}}\) exists. Then
\[
Y'=Y_d\oplus Y_{\mathrm{res}}
\]
is a totally isotropic complement to \(X\). Indeed, \(Y_d\subset U\) and \(Y_{\mathrm{res}}\subset U^\perp\), so the two summands are orthogonal. The pairing of \(Y_d\) with \(X\) is supported on \(X_U\), and it is perfect there by lemma `lem:package_subspace_endpoints`. The pairing of \(Y_{\mathrm{res}}\) with \(X\) is supported on \(X_{\mathrm{res}}\), and it is perfect there by construction inside \(U^\perp\). Thus \(Y'\) is dual to \(X=X_U\oplus X_{\mathrm{res}}\).

The affine slice \(x_0+\mathfrak u\) depends only on \(X\), not on the chosen complement, so relative to \(Y'\) the same element \(x\) again has the block form of lemma `lem:block_form_for_x0_plus_u`. Lemma `lem:package_complement` then applies with \(Y_U=Y_d\) and shows that \(U^\perp\) is \(x\)-stable and \(x|_{U^\perp}\) has the block form of a smaller problem with source dimension \(r-m\).

Finally,
\[
X^\perp=(U\cap X^\perp)\oplus (U^\perp\cap X^\perp).
\]
Indeed, write \(v\in X^\perp\) uniquely as
\[
v=u+u^\perp
\qquad(u\in U,\ u^\perp\in U^\perp).
\]
Because \(X_U\subset U\), every vector of \(U^\perp\) is automatically orthogonal to \(X_U\); since also \(v\perp X_U\), we get \(u\perp X_U\). Because \(X_{\mathrm{res}}\subset U^\perp\), every vector of \(U\) is automatically orthogonal to \(X_{\mathrm{res}}\); since also \(v\perp X_{\mathrm{res}}\), we get \(u^\perp\perp X_{\mathrm{res}}\). Hence \(u,u^\perp\in X^\perp\), proving the displayed decomposition.

Quotienting the displayed decomposition by \(X=X_U\oplus X_{\mathrm{res}}\), we obtain
\[
V_0
\cong
\bigl((U\cap X^\perp)/X_U\bigr)\oplus \bigl((U^\perp\cap X^\perp)/X_{\mathrm{res}}\bigr).
\]
Lemma `lem:package_subspace_middle_projection` identifies the first summand with \(D_U\). Because \(U\perp U^\perp\), the second summand is orthogonal to the first. Since \(U\) is nondegenerate and its chain model is exactly the first recursive move, that first summand is nondegenerate inside \(V_0\). Therefore the second summand is exactly its orthogonal complement
\[
D_U^\perp=D_{\mathrm{res}}.
\]
So the restricted nilpotent orbit \(x_{0,\mathrm{res}}\) on the smaller quotient has signed Young diagram obtained from \(D_0\) by deleting the rows consumed in the first recursive move.


# lemma lem:real_orbits_in_fixed_complex_orbit

## statement
Keep the classical real orthogonal/symplectic setting fixed in the problem statement, and let
\(\mathcal O_{\mathbb C}\subset \mathfrak g_{\mathbb C}\) be a complex nilpotent orbit. Then
\[
\mathcal O_{\mathbb C}(\mathbb R):=\mathcal O_{\mathbb C}\cap \mathfrak g(\mathbb R)
\]
is a finite union of real \(G\)-orbits, and each such real orbit is both open and closed in \(\mathcal O_{\mathbb C}(\mathbb R)\). In particular, two distinct real nilpotent orbits inside the same complex nilpotent orbit are not related by strict closure containment.

## proof
In this classical orthogonal/symplectic setting, real nilpotent \(G\)-orbits are parametrized by admissible signed Young diagrams. Fixing the complex nilpotent orbit \(\mathcal O_{\mathbb C}\) fixes its ordinary Jordan partition, and only finitely many admissible signings of that partition exist. Therefore
\[
\mathcal O_{\mathbb C}(\mathbb R)
\]
is a finite union of real \(G\)-orbits.

Let \(\sigma\) be complex conjugation on \(\mathfrak g_{\mathbb C}\), whose fixed points are
\(\mathfrak g(\mathbb R)\). The complex orbit \(\mathcal O_{\mathbb C}\) is a smooth complex
submanifold of \(\mathfrak g_{\mathbb C}\), stable under \(\sigma\), so its real locus
\[
\mathcal O_{\mathbb C}(\mathbb R)=\mathcal O_{\mathbb C}^{\sigma}
\]
is a smooth real submanifold, and for every \(x\in \mathcal O_{\mathbb C}(\mathbb R)\),
\[
T_x(\mathcal O_{\mathbb C}(\mathbb R))=T_x(\mathcal O_{\mathbb C})^{\sigma}.
\]
Now fix \(x\in \mathcal O_{\mathbb C}(\mathbb R)\). The tangent space to the complex orbit at
\(x\) is
\[
T_x(\mathcal O_{\mathbb C})=[\mathfrak g_{\mathbb C},x]=[\mathfrak g,x]\otimes_{\mathbb R}\mathbb C.
\]
Taking \(\sigma\)-fixed points gives
\[
T_x(\mathcal O_{\mathbb C}(\mathbb R))
=T_x(\mathcal O_{\mathbb C})^{\sigma}
=[\mathfrak g_{\mathbb C},x]^{\sigma}
=[\mathfrak g,x],
\]
which is also the tangent space to the real orbit \(G\cdot x\). Therefore \(G\cdot x\) is open in \(\mathcal O_{\mathbb C}(\mathbb R)\). Since \(\mathcal O_{\mathbb C}(\mathbb R)\) is a finite union of such open orbits, each one is also closed. So distinct real orbits inside \(\mathcal O_{\mathbb C}\) cannot lie in one another's closures.


# lemma lem:recursive_outputs_have_geometric_partition

## statement
Every signed Young diagram in \(\mathcal A(D_0,r)\) has underlying ordinary partition
\[
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X(\lambda_0+(2^r)).
\]

## proof
Forget the signs and write \(\lambda(D)\) for the ordinary partition underlying a signed diagram \(D\). We show by induction on \(r+\#\{\text{rows of }D\}\) that every output of \(\mathcal A(D,r)\) has partition
\[
\operatorname{collapse}_X(\lambda(D)+(2^r)).
\]
If \(r=0\), nothing changes. If \(D\) has no rows, rule \(3\) is exactly the \(d=0\) base case of the target-type collapse: in the symplectic family the partition is \((2^r)\), while in the orthogonal family it is \((2^{2a})\) for \(r=2a\) and \((2^{2a},1,1)\) for \(r=2a+1\).

Now assume that \(D\) has largest row length \(d\). Any partition obtained from \(\lambda(D)\) by adding \(2\) boxes to one row of length \(e<d\) is strictly dominated by the partition obtained by adding those same \(2\) boxes to a row of length \(d\). Therefore the first step of the target-type collapse must use the available length-\(d\) rows before it touches any shorter row. Let \(m\) be the number of source directions consumed at the first recursive step.

If \(d\) has free-sign parity, then rows of length \(d+2\) also have free-sign parity, so admissibility imposes no pairing constraint on the new longest rows. Hence the lexicographically maximal admissible first step is to replace exactly \(m\) rows of length \(d\) by \(m\) rows of length \(d+2\). This is precisely rule \(2\) in the free-sign case.

If \(d\) has forced-pair parity, write the length-\(d\) rows as \(s_d\) forced pairs. Then \(m=\min(r,2s_d)\). Admissibility requires rows of length \(d+2\) to occur in pairs. Therefore:
\[
m=2a \Longrightarrow \text{replace } a \text{ pairs } (d,d) \text{ by } a \text{ pairs } (d+2,d+2),
\]
\[
m=2a+1 \Longrightarrow \text{replace } a \text{ pairs } (d,d) \text{ by } a \text{ pairs } (d+2,d+2)
\]
and then use the last available source direction on one remaining pair \((d,d)\). For that last pair, the total box increase is \(2\), so the only candidates are \((d+2,d)\) and \((d+1,d+1)\). The partition \((d+2,d)\) is inadmissible because it contains a single row of the forced-parity length \(d+2\). Hence the unique maximal admissible correction is \((d,d)\mapsto (d+1,d+1)\). This is exactly rule \(2\) in the forced-pair case.

After this first step, the unmodified rows are exactly the rows of the recursive residual diagram, and the remaining quota is \(r-m\). Because every shorter-row modification yields a strictly smaller partition until all admissible length-\(d\) moves are exhausted, the rest of the target-type collapse is the collapse of that residual partition with quota \(r-m\). Thus the recursive algorithm computes the collapse step by step. The induction hypothesis applied to the residual problem gives the claimed formula for every output of \(\mathcal A(D,r)\). Applying this to \((D_0,r)\) gives \(\lambda_{\mathrm{geom}}\).


# lemma lem:uniform_recursive_outputs_have_geometric_partition

## statement
Let \(D\) be any admissible signed Young diagram, and let \(r\ge 0\). Then every signed Young
diagram in \(\mathcal A(D,r)\) has underlying ordinary partition
\[
\operatorname{collapse}_X(\lambda(D)+(2^r)).
\]

## proof
Apply lemma `lem:recursive_outputs_have_geometric_partition` to the present recursive problem after
renaming the current admissible input diagram \(D\) as \(D_0\). In that lemma, \(\lambda_0\)
denotes the ordinary partition underlying the chosen input diagram, so for this renaming we have
\[
\lambda_0=\lambda(D).
\]
Therefore the conclusion of lemma `lem:recursive_outputs_have_geometric_partition` becomes exactly
\[
\operatorname{collapse}_X(\lambda(D)+(2^r)),
\]
which is the claimed partition for every element of \(\mathcal A(D,r)\).


# lemma lem:lambda_geom_points_are_maximal

## statement
If \(x\in x_0+\mathfrak u\) has ordinary partition \(\lambda_{\mathrm{geom}}\), then its real orbit \(G\cdot x\) is \(\preceq\)-maximal in
\[
\operatorname{Ind}_P^G(\mathcal O_0)=\overline{\,G\cdot(\mathcal O_0+\mathfrak u)\,}.
\]

## proof
Let \(\mathcal O=G\cdot x\), and suppose \(\mathcal O\subseteq \overline{\mathcal O'}\) for some real orbit \(\mathcal O'\) in the induced closure. Let
\[
\Theta=\mathcal O_{\mathbb C},\qquad \Theta'=\mathcal O'_{\mathbb C}
\]
be the corresponding complex nilpotent orbits. Because \(x\in \overline{\mathcal O'}\subset \overline{\Theta'}\) and \(\overline{\Theta'}\) is \(G_{\mathbb C}\)-stable, we get
\[
\Theta=G_{\mathbb C}\cdot x\subseteq \overline{\Theta'}.
\]

Let
\[
Z_{\mathbb C}:=\overline{\,G_{\mathbb C}\cdot(x_0+\mathfrak u_{\mathbb C})\,}.
\]
Then \(\Theta,\Theta'\subset Z_{\mathbb C}\). By the geometric-partition fact allowed in the problem statement, the unique maximal complex partition occurring in \(Z_{\mathbb C}\) is
\[
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X(\lambda_0+(2^r)).
\]
Since \(\Theta\) already has partition \(\lambda_{\mathrm{geom}}\), the dominance order on complex classical nilpotent orbits forces \(\Theta'\) to have the same partition \(\lambda_{\mathrm{geom}}\). For classical complex groups, the dimension of a nilpotent orbit depends only on its partition; in type \(D\), the two very-even orbits attached to one partition still have the same dimension. Hence
\[
\dim \Theta=\dim \Theta'.
\]
A complex orbit cannot be a proper subset of the closure of another complex orbit of the same dimension, so \(\Theta=\Theta'\).

Thus \(\mathcal O\) and \(\mathcal O'\) are real orbits inside the same complex nilpotent orbit. By lemma `lem:real_orbits_in_fixed_complex_orbit`, two distinct such real orbits are not in a strict closure relation. Since \(\mathcal O\subseteq \overline{\mathcal O'}\), we conclude \(\mathcal O=\mathcal O'\). Therefore \(\mathcal O\) is \(\preceq\)-maximal.


# lemma lem:maximal_partition_forces_maximal_highest_degree_package

## statement
Let \(x\in x_0+\mathfrak u\) have ordinary partition \(\lambda_{\mathrm{geom}}\). Let \(d\ge 1\) be the largest row length in \(D_0\), put
\[
m:=\min(r,\dim M_d),
\]
and let \(m'=\operatorname{rank}(B_d)\). Choose \(Y_d\subset Y\) of dimension \(m'\) such that \(B_d|_{Y_d}\) is injective. Then \(m'=m\), and \(q_d|_{Y_d}\) has maximal possible Witt rank:

1. if \(d\) has free-sign parity, then \(q_d|_{Y_d}\) is nondegenerate;
2. if \(d\) has forced-pair parity, then \(q_d|_{Y_d}\) has rank \(m'\) when \(m'\) is even and rank \(m'-1\) when \(m'\) is odd.

Consequently the highest-degree package of \(x\) is exactly one of the recursive moves in the definition of \(\mathcal A(D_0,r)\).

## proof
Choose a complementary subspace \(Y=Y_d\oplus \ker(B_d)\). For every \(y\in \ker(B_d)\), the top degree-\(d\) component of \(B(y)\) vanishes, so
\[
B(y)\in (M_d\otimes x_0W_d)\oplus V_{<d}.
\]
The block calculation from the proof of lemma `lem:highest_degree_power_formula` gives
\[
x^{k+1}y=x_0^kB(y)+A\!\bigl(x_0^{k-1}B(y)\bigr)
\qquad(k\ge 1).
\]
Since \(x_0^{d-1}\) kills \(M_d\otimes x_0W_d\) and every chain in \(V_{<d}\) has length at most \(d-1\), we get
\[
x_0^{d-1}B(y)=x_0^dB(y)=0,
\]
hence
\[
x^{d+1}y=0.
\]
Thus no source vector outside \(Y_d\) can contribute a row of length \(d+2\).

Now compare with the recursive rule. By lemma `lem:recursive_outputs_have_geometric_partition`, the partition \(\lambda_{\mathrm{geom}}\) is obtained by using all \(m\) available degree-\(d\) source directions before any shorter degree can contribute rows of length \(d+2\). If \(m'<m\), then only \(m'\) source directions can create rows of length \(d+2\), while every vector in \(\ker(B_d)\) creates rows of length at most \(d+1\). The resulting partition is therefore strictly smaller than \(\lambda_{\mathrm{geom}}\), contradiction. Thus \(m'=m\).

Assume now \(m'=m\).
Conjugating by the element supplied by lemma `lem:highest_degree_tail_clearing` does not change \(B_d|_{Y_d}\), hence does not change \(q_d|_{Y_d}\) or the signed Young diagram of \(x\). So we may also assume
\[
B(Y_d)\subset M_d\otimes W_d.
\]
By lemma `lem:highest_degree_general_package_moves`, the highest-degree rows coming from \(Y_d\) are read off directly from the Witt decomposition of \(q_d\).

In the free-sign case, each nondegenerate symmetric Witt line gives one row of length \(d+2\), while a symmetric radical line gives only a forced pair of length \(d+1\). Because no vector in \(\ker(B_d)\) can create a row of length \(d+2\), the presence of a symmetric radical line would reduce the multiplicity of the longest rows below that of \(\lambda_{\mathrm{geom}}\). So \(q_d|_{Y_d}\) must be nondegenerate.

In the forced-pair case, each alternating Witt \(2\)-block gives one pair of rows of length \(d+2\), while a radical line gives only the cutoff pair of length \(d+1,d+1\). Therefore the lexicographically largest partition obtainable from \(m\) degree-\(d\) source directions is achieved exactly when \(q_d|_{Y_d}\) has maximal possible rank: rank \(m\) if \(m\) is even and rank \(m-1\) if \(m\) is odd. Any further rank defect replaces at least one expected length-\((d+2)\) pair by rows of length at most \(d+1\), and no vector in \(\ker(B_d)\) can repair that loss. Hence \(q_d|_{Y_d}\) has maximal possible Witt rank.

If \(d\) has free-sign parity, let \(p_d^+\) and \(p_d^-\) be the numbers of \(+\)- and \(-\)-rows of length \(d\) in \(D_0\). The now-nondegenerate symmetric form \(q_d|_{Y_d}\) has some signature \((a,b)\) with \(a+b=m\). Because
\[
q_d(y,y')=\phi_d(B_dy,B_dy'),
\]
this is the signature of the selected \(m\)-dimensional subspace \(B_d(Y_d)\subset M_d\), so \(a\le p_d^+\) and \(b\le p_d^-\). Lemma `lem:highest_degree_general_package_moves` therefore produces exactly \(a\) length-\((d+2)\) rows with flipped sign from \(+\)-rows and \(b\) such rows from \(-\)-rows, which is one allowed recursive choice. If \(d\) has forced-pair parity, maximal Witt rank means precisely \(\lfloor m/2\rfloor\) alternating \(2\)-blocks and, when \(m\) is odd, one radical line; by the same lemma this gives exactly the even or odd recursive move. Therefore the highest-degree package of \(x\) is exactly one recursive move.


# lemma lem:first_step_residual_collapse

## statement
Let \(D\) be an admissible signed Young diagram with largest row length \(d\ge 1\), and let
\(r>0\). Consider one admissible first recursive move in the definition of \(\mathcal A(D,r)\).
Let \(m\) be the number of source directions consumed at that first step, let \(D_{\mathrm{res}}\)
be the residual signed diagram obtained by deleting the consumed length-\(d\) rows or forced
pairs, and let \(\lambda_{\mathrm{new}}\) be the ordinary partition contributed by that first
step:

1. in the free-sign case, \(\lambda_{\mathrm{new}}\) consists of \(m\) rows of length \(d+2\);
2. in the forced-pair case with \(m=2a\), \(\lambda_{\mathrm{new}}\) consists of \(2a\) rows of
   length \(d+2\);
3. in the forced-pair case with \(m=2a+1\), \(\lambda_{\mathrm{new}}\) consists of \(2a\) rows
   of length \(d+2\) together with two rows of length \(d+1\).

Then
\[
\operatorname{collapse}_X(\lambda(D)+(2^r))
=
\lambda_{\mathrm{new}}\sqcup
\operatorname{collapse}_X(\lambda(D_{\mathrm{res}})+(2^{\,r-m})).
\]

## proof
Choose any signed diagram
\[
E_{\mathrm{res}}\in \mathcal A(D_{\mathrm{res}},r-m).
\]
Such a diagram exists because the recursive definition of \(\mathcal A\) always terminates at the
base case \(r=0\) or at rule \(3\) when no rows remain.

Let \(P_{\mathrm{new}}\) be the signed diagram contributed by the chosen first recursive move. By
construction, its ordinary partition is exactly \(\lambda_{\mathrm{new}}\). Again by the recursive
definition of \(\mathcal A(D,r)\), the multiset union
\[
P_{\mathrm{new}}\sqcup E_{\mathrm{res}}
\]
is an element of \(\mathcal A(D,r)\).

Apply lemma `lem:uniform_recursive_outputs_have_geometric_partition` first to the residual problem
and then to the full problem. It gives
\[
\lambda(E_{\mathrm{res}})
=
\operatorname{collapse}_X(\lambda(D_{\mathrm{res}})+(2^{\,r-m}))
\]
and
\[
\lambda(P_{\mathrm{new}}\sqcup E_{\mathrm{res}})
=
\operatorname{collapse}_X(\lambda(D)+(2^r)).
\]
But the ordinary partition of a multiset union of signed diagrams is the multiset union of the
ordinary partitions, so
\[
\lambda(P_{\mathrm{new}}\sqcup E_{\mathrm{res}})
=
\lambda_{\mathrm{new}}\sqcup \lambda(E_{\mathrm{res}}).
\]
Substituting the residual identity into the full identity yields
\[
\operatorname{collapse}_X(\lambda(D)+(2^r))
=
\lambda_{\mathrm{new}}\sqcup
\operatorname{collapse}_X(\lambda(D_{\mathrm{res}})+(2^{\,r-m})),
\]
which is the desired formula.


# proposition prop:slice_geometric_partition_classification

## statement
Let
\[
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X(\lambda_0+(2^r)).
\]
Then the signed Young diagrams of the slice points
\[
x\in x_0+\mathfrak u
\quad\text{with ordinary partition }\lambda_{\mathrm{geom}}
\]
are exactly the diagrams in \(\mathcal A(D_0,r)\).

## proof
We argue by induction on
\[
N:=r+\#\{\text{rows of }D_0\}.
\]
If \(r=0\), then \(x_0+\mathfrak u=\{x_0\}\), so the only signed Young diagram is \(D_0\), namely
\(\mathcal A(D_0,0)\).

Assume \(r>0\). If \(D_0\) has no rows, then \(V_0=0\) and \(x_0=0\). Every slice point
\(x\in x_0+\mathfrak u\) therefore has \(B=0\), so by lemma
`lem:block_form_for_x0_plus_u` it is determined by the map \(C:Y\to X\), equivalently by the form
\[
c(y,y'):=\langle y,Cy'\rangle
\]
on \(Y\), satisfying
\[
c(y,y')+\epsilon\,c(y',y)=0.
\]
If \(Y=Y_1\oplus Y_2\) is an orthogonal decomposition for \(c\), and
\[
X=X_1\oplus X_2
\]
is the dual decomposition, then \(C(Y_i)\subset X_i\): indeed, for \(y\in Y_i\) and
\(y'\in Y_{3-i}\),
\[
\langle y',Cy\rangle=c(y',y)=0,
\]
so \(Cy\) pairs trivially with \(Y_{3-i}\) and therefore lies in \(X_i\). Thus a Witt
decomposition of \(c\) splits \(x\) into an orthogonal direct sum of isolated zero-row
packages, and lemma `lem:zero_row_package_moves` applies block by block.

In the symplectic family, \(\epsilon=-1\), so \(c\) is symmetric. Choose a Witt decomposition
\[
Y=\bigoplus_{i=1}^s \mathbb R y_i \oplus \bigoplus_{j=1}^t \mathbb R z_j,
\]
where the lines \(\mathbb R y_i\) are nondegenerate and the lines \(\mathbb R z_j\) span
\(\operatorname{rad}(c)\). Let \(\mathbb R x_i\) and \(\mathbb R x_{z_j}\) be the corresponding
dual lines in \(X\). Then
\[
V=\bigoplus_{i=1}^s (\mathbb R x_i\oplus \mathbb R y_i)
\oplus
\bigoplus_{j=1}^t (\mathbb R x_{z_j}\oplus \mathbb R z_j)
\]
is an orthogonal direct sum of \(x\)-stable zero-row blocks. By lemma
`lem:zero_row_package_moves`, each nondegenerate line \(\mathbb R y_i\) contributes one row of
length \(2\), while each radical line \(\mathbb R z_j\) contributes one forced \(+/-\) pair of
rows of length \(1\). Hence
\[
\lambda(x)=(2^s,1^{2t}).
\]
For \(D_0=\varnothing\) in type \(C\),
\[
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X((2^r)),
\]
and this partition is simply \((2^r)\). Therefore \(\lambda(x)=\lambda_{\mathrm{geom}}\) forces
\(t=0\) and \(s=r\). The signs of those \(r\) rows of length \(2\) are exactly the signs of the
nondegenerate Witt lines of \(c\), so the possible signed diagrams are precisely the rule-\(3\)
outputs
\[
[2]_+^{\,a}\sqcup [2]_-^{\,b}
\qquad(a+b=r).
\]
Conversely, every such output is realized by taking \(c\) nondegenerate symmetric of signature
\((a,b)\).

In the orthogonal family, \(\epsilon=+1\), so \(c\) is alternating. Choose a Witt decomposition
\[
Y=\bigoplus_{i=1}^a Y_i \oplus \bigoplus_{j=1}^t \mathbb R z_j,
\]
where each \(Y_i\) is a symplectic \(2\)-block for \(c\) and the lines \(\mathbb R z_j\) span
\(\operatorname{rad}(c)\). With the dual decomposition
\[
X=\bigoplus_{i=1}^a X_i \oplus \bigoplus_{j=1}^t \mathbb R x_{z_j},
\]
the space
\[
V=\bigoplus_{i=1}^a (X_i\oplus Y_i)
\oplus
\bigoplus_{j=1}^t (\mathbb R x_{z_j}\oplus \mathbb R z_j)
\]
is again an orthogonal direct sum of isolated zero-row packages. Lemma
`lem:zero_row_package_moves` gives one forced \(+/-\) pair of rows of length \(2\) from each
symplectic block \(Y_i\), and two rows of length \(1\) with opposite signs from each radical line.
Thus
\[
\lambda(x)=(2^{2a},1^{2t}),
\qquad
2a+t=r.
\]
For \(D_0=\varnothing\) in type \(B\) or \(D\),
\[
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X((2^r))
=
\begin{cases}
(2^r), & r \text{ even},\\
(2^{r-1},1,1), & r \text{ odd}.
\end{cases}
\]
Hence \(\lambda(x)=\lambda_{\mathrm{geom}}\) forces \(t=0\) when \(r\) is even and \(t=1\) when
\(r\) is odd. The resulting signed diagrams are therefore exactly the rule-\(3\) outputs: \(a\)
forced \(+/-\) pairs of length \(2\), and in the odd case one additional opposite-sign pair of
rows of length \(1\). Conversely, these outputs are realized by taking \(c\) to be the direct sum
of \(a\) symplectic \(2\)-blocks and, when \(r\) is odd, one zero line. Hence, when \(D_0\) has
no rows, the slice diagrams of partition \(\lambda_{\mathrm{geom}}\) are precisely
\(\mathcal A(D_0,r)\).

Now let \(d\ge 1\) be the largest row length in \(D_0\).

For existence, take \(E\in \mathcal A(D_0,r)\). By the recursive definition, there is a first
recursive move with package diagram \(P_{\mathrm{new}}\), residual signed diagram
\(D_{\mathrm{res}}\), consumed quota \(m\ge 1\), and residual output
\[
E_{\mathrm{res}}\in \mathcal A(D_{\mathrm{res}},r-m)
\]
such that
\[
E=P_{\mathrm{new}}\sqcup E_{\mathrm{res}}.
\]
Using lemma `lem:signed_chain_model`, split the chain model of \(x_0\) orthogonally into the rows
consumed by this move and the residual rows:
\[
V_0=V_{0,U}\oplus V_{0,\mathrm{res}},
\qquad
x_0=x_{0,U}\oplus x_{0,\mathrm{res}},
\]
where the residual summand has signed diagram \(D_{\mathrm{res}}\). Choose
\[
X=X_U\oplus X_{\mathrm{res}},
\qquad
Y=Y_U\oplus Y_{\mathrm{res}},
\qquad
\dim Y_U=m.
\]
Write
\[
V_U:=X_U\oplus V_{0,U}\oplus Y_U,
\qquad
V_{\mathrm{res}}:=X_{\mathrm{res}}\oplus V_{0,\mathrm{res}}\oplus Y_{\mathrm{res}}.
\]
Decompose \(V_U\) further as an orthogonal direct sum of the individual package blocks used in the
chosen first recursive move:
\[
V_U=\bigoplus_{\nu} V_{\nu},
\qquad
V_{\nu}=X_{\nu}\oplus V_{0,\nu}\oplus Y_{\nu},
\]
where \(Y_{\nu}\) has dimension \(1\) in the free-sign and alternating-radical packages and
dimension \(2\) in the alternating-nondegenerate package. On each \(V_{\nu}\), define \(x_{\nu}\)
explicitly by keeping \(x_{0,U}\) on the chain part \(V_{0,\nu}\), by taking \(C_{\nu}=0\), and by
choosing the \(B\)-map exactly as in the local normal form for the corresponding package:

1. in the free-sign case, if \(Y_{\nu}=\mathbb R y_{\nu}\), send \(y_{\nu}\) to the chosen top
   vector \(t_{\nu}\) of the consumed length-\(d\) row;
2. in the alternating-nondegenerate case, if
\[
Y_{\nu}=\mathbb R y_{\nu}\oplus \mathbb R y_{\nu}',
\]
send \(y_{\nu}\mapsto t_{+,\nu}\) and \(y_{\nu}'\mapsto t_{-,\nu}\);
3. in the alternating-radical case, if \(Y_{\nu}=\mathbb R y_{\nu}\), send \(y_{\nu}\mapsto t_{+,\nu}\).

With these choices, lemma `lem:block_form_for_x0_plus_u` determines
\[
A_{\nu}=-B_{\nu}^{\sharp},
\]
so \(x_{\nu}\) lies in the local slice \(x_{0,\nu}+\mathfrak u_{\nu}\). Proposition
`prop:local_package_moves` computes the signed Young diagram of \(x_{\nu}\): it is exactly the
piece of \(P_{\mathrm{new}}\) attached to the package \(\nu\). Because the summands \(V_{\nu}\) are
mutually orthogonal and each \(x_{\nu}\) has block form relative to
\[
X_{\nu}\oplus V_{0,\nu}\oplus Y_{\nu},
\]
their direct sum
\[
x_U:=\bigoplus_{\nu} x_{\nu}
\]
again has the block form of lemma `lem:block_form_for_x0_plus_u` relative to
\[
X_U\oplus V_{0,U}\oplus Y_U.
\]
Hence
\[
x_U\in x_{0,U}+\mathfrak u_U,
\]
and its signed Young diagram is exactly \(P_{\mathrm{new}}\). The residual slice problem has
induction parameter
\((r-m)+\#\{\text{rows of }D_{\mathrm{res}}\}<N\), so the induction hypothesis yields
\[
x_{\mathrm{res}}\in x_{0,\mathrm{res}}+\mathfrak u_{\mathrm{res}}
\]
with signed diagram \(E_{\mathrm{res}}\) and ordinary partition
\[
\operatorname{collapse}_X(\lambda(D_{\mathrm{res}})+(2^{\,r-m})).
\]
Then
\[
x:=x_U\oplus x_{\mathrm{res}}\in x_0+\mathfrak u
\]
because \(V_U\perp V_{\mathrm{res}}\), both summands have the block form from lemma
`lem:block_form_for_x0_plus_u` on their own orthogonal decompositions, and their \(B\)- and
\(C\)-blocks are block diagonal with respect to
\[
Y=Y_U\oplus Y_{\mathrm{res}},
\qquad
X=X_U\oplus X_{\mathrm{res}}.
\]
Its signed Young diagram is the multiset union
\[
P_{\mathrm{new}}\sqcup E_{\mathrm{res}}=E.
\]
Since \(E\in \mathcal A(D_0,r)\), lemma
`lem:recursive_outputs_have_geometric_partition` gives
\[
\lambda(x)=\lambda(E)=\lambda_{\mathrm{geom}},
\]
so \(E\) occurs on a slice point of partition \(\lambda_{\mathrm{geom}}\).

For necessity, let
\[
x\in x_0+\mathfrak u
\]
have ordinary partition \(\lambda_{\mathrm{geom}}\). Choose \(Y_d\subset Y\) so that
\(B_d|_{Y_d}\) is injective. Lemma `lem:maximal_partition_forces_maximal_highest_degree_package`
shows that the highest-degree package of \(x\) is an admissible first recursive move. After
conjugating by lemma `lem:highest_degree_tail_clearing`, we may assume
\[
B(Y_d)\subset M_d\otimes W_d.
\]
Proposition `prop:highest_degree_reduction` then gives an orthogonal decomposition
\[
x=x_U\oplus x_{\mathrm{res}}
\]
in which \(x_U\) has exactly that first recursive package, while \(x_{\mathrm{res}}\) lies in a
smaller slice with source dimension \(r-m\) and base signed diagram \(D_{\mathrm{res}}\).

Let \(\lambda_{\mathrm{new}}:=\lambda(x_U)\). Because ordinary partitions add under orthogonal
direct sums,
\[
\lambda_{\mathrm{geom}}=\lambda(x)=\lambda_{\mathrm{new}}\sqcup \lambda(x_{\mathrm{res}}).
\]
Lemma `lem:first_step_residual_collapse` gives the companion identity
\[
\lambda_{\mathrm{geom}}
=
\lambda_{\mathrm{new}}\sqcup
\operatorname{collapse}_X(\lambda(D_{\mathrm{res}})+(2^{\,r-m})).
\]
Hence
\[
\lambda(x_{\mathrm{res}})
=
\operatorname{collapse}_X(\lambda(D_{\mathrm{res}})+(2^{\,r-m})).
\]
The residual slice problem again has smaller induction parameter, so the induction hypothesis
implies
\[
\text{signed diagram}(x_{\mathrm{res}})\in \mathcal A(D_{\mathrm{res}},r-m).
\]
Reattaching the admissible first recursive package \(x_U\) shows
\[
\text{signed diagram}(x)\in \mathcal A(D_0,r).
\]
Therefore the slice diagrams of partition \(\lambda_{\mathrm{geom}}\) are exactly
\(\mathcal A(D_0,r)\).


# theorem thm:recursive_signed_induction

## statement
Assume the geometric-partition input from the problem statement:
every \(\preceq\)-maximal real orbit in
\[
\operatorname{Ind}_P^G(\mathcal O_0)
\]
has ordinary partition
\[
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X(\lambda_0+(2^r)).
\]

Define recursively a set \(\mathcal A(D,r)\) of admissible signed Young diagrams.
Write \(\sqcup\) for multiset union of rows.

1. If \(r=0\), set \(\mathcal A(D,0)=\{D\}\).
2. Suppose \(r>0\) and \(D\) has a largest row length \(d\ge 1\).
   - If \(d\) has free-sign parity, let \(p_d^+,p_d^-\) be the numbers of \(+\)- and \(-\)-rows of length \(d\), put
\[
m=\min(r,p_d^++p_d^-),
\]
and choose \(a,b\) with \(a+b=m\), \(a\le p_d^+\), \(b\le p_d^-\).
Let \(D_{\mathrm{res}}\) be obtained from \(D\) by deleting \(a\) \(+\)-rows and \(b\) \(-\)-rows of length \(d\), and let \(P_{\mathrm{new}}\) be the diagram consisting of \(a\) \(-\)-rows and \(b\) \(+\)-rows of length \(d+2\).
For this choice, the outputs are
\[
P_{\mathrm{new}}\sqcup E
\qquad(E\in \mathcal A(D_{\mathrm{res}},r-m)).
\]
Take the union over all admissible choices of \(a,b\).
   - If \(d\) has forced-pair parity, let \(s_d\) be the number of forced \(+/-\) pairs of length \(d\), and put
\[
m=\min(r,2s_d).
\]
If \(m=2a\), let \(D_{\mathrm{res}}\) be obtained from \(D\) by deleting \(a\) forced \(+/-\) pairs of length \(d\), and let \(P_{\mathrm{new}}\) consist of \(a\) forced \(+/-\) pairs of length \(d+2\).
If \(m=2a+1\), let \(D_{\mathrm{res}}\) be obtained from \(D\) by deleting \(a+1\) forced \(+/-\) pairs of length \(d\), and let \(P_{\mathrm{new}}\) consist of \(a\) forced \(+/-\) pairs of length \(d+2\) together with two rows of length \(d+1\) with opposite signs.
For each admissible choice, the outputs are
\[
P_{\mathrm{new}}\sqcup E
\qquad(E\in \mathcal A(D_{\mathrm{res}},r-m)).
\]
Take the union over all admissible choices of the consumed length-\(d\) pairs.
3. If \(r>0\) and \(D\) has no rows:
   - in the symplectic family, choose any \(a+b=r\) and output \(a\) rows \( [2]_+ \) and \(b\) rows \( [2]_- \);
   - in the orthogonal family, if \(r=2a\) output \(a\) forced \(+/-\) pairs of length \(2\), and if \(r=2a+1\) output those pairs together with two rows of length \(1\) of opposite signs.

Then the \(\preceq\)-maximal real induced orbits in \(\operatorname{Ind}_P^G(\mathcal O_0)\) are exactly the real nilpotent orbits whose signed Young diagrams lie in \(\mathcal A(D_0,r)\).

## proof
Proposition `prop:slice_geometric_partition_classification` identifies the
diagrams in \(\mathcal A(D_0,r)\) exactly with the signed Young diagrams of the
slice points
\[
x\in x_0+\mathfrak u
\]
whose ordinary partition is \(\lambda_{\mathrm{geom}}\).

If \(E\in \mathcal A(D_0,r)\), choose such a slice point \(x\). Lemma
`lem:lambda_geom_points_are_maximal` then shows that the real orbit \(G\cdot x\)
is \(\preceq\)-maximal in \(\operatorname{Ind}_P^G(\mathcal O_0)\). So every
diagram in \(\mathcal A(D_0,r)\) occurs on a maximal real induced orbit.

Conversely, let \(\mathcal O\) be a \(\preceq\)-maximal real induced orbit. By
the geometric-partition hypothesis stated at the start of the theorem, every
point of \(\mathcal O\) has ordinary partition \(\lambda_{\mathrm{geom}}\). By
lemma `lem:maximal_orbits_vs_open_subsets`, the set
\[
\mathcal O\cap S,
\qquad
S=\mathcal O_0+\mathfrak u,
\]
contains a nonempty open subset of \(S\). Lemma
`lem:open_in_S_gives_open_in_slice` therefore implies that
\[
\mathcal O\cap (x_0+\mathfrak u)
\]
contains a nonempty open subset of the slice. Choose
\[
x\in \mathcal O\cap(x_0+\mathfrak u).
\]
Then \(x\) has ordinary partition \(\lambda_{\mathrm{geom}}\), so proposition
`prop:slice_geometric_partition_classification` gives
\[
\text{signed diagram}(x)\in \mathcal A(D_0,r).
\]
Thus the \(\preceq\)-maximal real induced orbits are exactly the real nilpotent
orbits whose signed Young diagrams lie in \(\mathcal A(D_0,r)\).


# theorem thm:main

## statement
 # Real Nilpotent Orbit Induction: General Signed-Diagram Problem

This file formulates the signed-diagram part of the real induced-orbit problem. The goal is to obtain an algorithmic description of the `\preceq`-maximal real induced orbits in terms of signed Young diagrams, in the full scope of the problem as stated here.

## Setup

Let $F=\mathbb{R}$, and let $(V,\langle\cdot,\cdot\rangle)$ be a real formed space of parity $\epsilon\in\{+1,-1\}$:

- if $\epsilon=+1$, then $G=\mathrm{Isom}(V)\cong O(p,q)$ for a symmetric form of signature $(p,q)$;
- if $\epsilon=-1$, then $G=\mathrm{Isom}(V)\cong Sp(2n,\mathbb R)$ for a non-degenerate alternating form.

Let $X\subset V$ be a totally isotropic subspace of dimension $r\ge 1$, let
$$
V_0=X^\perp/X,
$$
and let $G_0=\mathrm{Isom}(V_0)$. The stabilizer $P=\operatorname{Stab}_G(X)$ is a maximal parabolic with Levi factor
$$
L\cong GL(X)\times G_0
$$
and unipotent radical with Lie algebra $\mathfrak u$.

Let $\mathcal O_0$ be a real nilpotent orbit of $G_0$, let $D_0$ be its admissible signed Young diagram, and let $\lambda_0$ be the underlying ordinary partition.

Define
$$
\operatorname{Ind}_P^G(\mathcal O_0):=\overline{\,G\cdot(\mathcal O_0+\mathfrak u)\,}.
$$

Define the closure order on real nilpotent orbits by
$$
\mathcal O' \preceq \mathcal O
\quad\Longleftrightarrow\quad
\mathcal O' \subseteq \overline{\mathcal O}.
$$

## Signed Young Diagrams

Real nilpotent orbits are parameterized by admissible signed Young diagrams.

A signed Young diagram consists of:

- an ordinary partition;
- an alternating sign pattern on each row;
- the leftmost sign on each row.

Admissibility is:

- for $O(p,q)$:
  - even row lengths occur in `$+/-$' pairs;
  - odd row lengths carry the free sign data;
  - the total numbers of `$+$' and `$-$' boxes are $p$ and $q$;

- for $Sp(2n,\mathbb R)$:
  - odd row lengths occur in `$+/-$' pairs;
  - even row lengths carry the free sign data;
  - the total numbers of `$+$' and `$-$' boxes are both $n$.

Two signed Young diagrams are regarded as the same if they differ only by permuting rows of the same length and sign type.

## Given

You may take the underlying geometric partition as known:
$$
\lambda_{\mathrm{geom}}=\operatorname{collapse}_X(\lambda_0+(2^r)),
$$
where the target-type collapse is:

- $C$ in the symplectic case;
- $B$ in the odd orthogonal case;
- $D$ in the even orthogonal case.

Thus every `\preceq`-maximal real induced orbit lies over the fixed partition $\lambda_{\mathrm{geom}}$.

## Main Problem

Give a general algorithm that takes as input:

- the family (`symplectic`, `odd orthogonal`, or `even orthogonal`);
- the input signed Young diagram $D_0$;
- the integer $r$;

and outputs exactly the admissible signed Young diagrams that occur as `\preceq`-maximal real induced orbits in
$$
\operatorname{Ind}_P^G(\mathcal O_0).
$$

The algorithm must genuinely depend on the input signed Young diagram $D_0$, not merely on the underlying partition $\lambda_0$ and final admissibility.

The goal is an algorithmic theorem for the problem as stated here, not a low-rank example and not a special-case classification substituted in place of the main problem.

## Proof Standard

Your proof must be down to earth and as self-contained as possible.

You may freely use only standard classical mathematics without proof. Examples include:

- Jordan normal form;
- basic linear algebra over $\mathbb R$ and $\mathbb C$;
- basic properties of orthogonal and symplectic forms;
- the standard parametrization of real nilpotent orbits by admissible signed Young diagrams, provided you restate the exact version you use.

If you use any result that is not a standard classical fact, then you must do one of the following:

1. prove it in the special case needed here; or
2. reduce the argument to a statement that is proved directly in this file.

In particular:

- do not treat a specialized modern theorem as a black box;
- do not cite a family-specific induction algorithm unless you also supply a self-contained proof of the part you need;
- do not rely on a theorem merely because it appears relevant in the literature;
- if a statement is only proved in a restricted range or family, you must say so explicitly and restrict the conclusion accordingly;
- if you inspect an external theorem, you must inspect its proof before relying on it;
- if the external proof is incomplete, sketchy, or depends on nonstandard unproved inputs, then you may not rely on that theorem as established.

## What You Should Do

Analyze the problem above. The target is an algorithmic answer to the stated problem itself.

If you cannot prove a full answer to the stated theorem, you must say exactly where the proof breaks and why. Do not replace the main goal by a low-rank special case, and do not substitute a weak counterexample-only result for the requested theorem.

## Important Scope Boundary

- Do not downgrade the target to a special case unless you explicitly label it as a failed attempt.
- Do not weaken the problem by silently replacing it with an easier nearby question.
- Do not claim a full answer unless every step is justified directly in the draft.
- Multiplicity questions are excluded.

## proof
The `Given` paragraph of the problem statement supplies exactly the
geometric-partition hypothesis recorded at the start of theorem
`thm:recursive_signed_induction`. Applying that theorem in the present
orthogonal/symplectic setup gives the requested algorithmic description of the
\(\preceq\)-maximal real induced orbits. Hence it solves the main problem as
stated.
