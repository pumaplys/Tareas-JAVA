package tema2.t03;

public class ej06 {
    public static void main(String[] args) {

        int num1 = 3;
        int num2 = 4;
        int num3 = 0;

        int mayor = MayorTresNumeros(num1, num2, num3);

        System.out.println("Mayor: " + mayor);
    }

    static int MayorTresNumeros(int a, int b, int c) {
        if (a >= b && a >= c) {
            return a;
        }
        else if  (b >= a && b >= c) {
            return b;
        } else {
            return c;
        }
    }
}
